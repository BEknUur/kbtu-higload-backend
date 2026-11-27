#!/usr/bin/env bash
# Usage: bash run.sh [stage ...]
# Stages: baseline indexed partitioned pooled replica. Each stage = fresh database + all changes up to it.
set -euo pipefail
cd "$(dirname "$0")"
source .env

STAGES=${*:-baseline indexed partitioned pooled replica}
RATES=${RATES:-500 1000 2000}
LOGIN="$POSTGRES_USER:$POSTGRES_PASSWORD"
DIRECT="postgres://$LOGIN@primary:5432/$POSTGRES_DB"
BOUNCER="postgres://$LOGIN@pgbouncer:6432/$POSTGRES_DB"
BOUNCER_RO="postgres://$LOGIN@pgbouncer:6432/${POSTGRES_DB}_ro"

sql() {
  docker compose exec -T primary psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"
}

bench() {  
  docker compose run --rm -T -e POOL="${POOL:-80}" \
    -e STAGE="$1" -e RATE="$2" -e READ_URL="$3" -e WRITE_URL="$4" \
    bench
}

prepare() { 
  sql < sql/01_schema.sql
  sql < sql/02_seed.sql
  if [[ $1 != baseline ]]; then
    sql < sql/03_indexes.sql
  fi
  if [[ $1 =~ partitioned|pooled|replica ]]; then
    sql < sql/04_partitioning.sql
  fi
  sql < sql/05_explain.sql > "results/$1-explain.txt"
}

lag_monitor() {  
  while true; do
    sql -Atc "SELECT now()::time(0), write_lag, replay_lag FROM pg_stat_replication" || true
    sleep 1
  done
}

docker compose down -v --remove-orphans
docker compose up -d --wait primary
mkdir -p results
if [[ ! -f results/summary.csv ]]; then
  echo "stage,rate,query,count,p50_ms,p95_ms,p99_ms,max_ms,errors,backends" > results/summary.csv
fi

for stage in $STAGES; do
  read_url=$DIRECT
  write_url=$DIRECT
  case $stage in
    pooled)
      docker compose up -d --wait pgbouncer
      read_url=$BOUNCER
      write_url=$BOUNCER
      ;;
    replica)
      docker compose up -d --wait --force-recreate pgbouncer replica
      read_url=$BOUNCER_RO
      write_url=$BOUNCER
      lag_monitor > results/replica-lag.txt &
      monitor=$!
      ;;
  esac

  echo "=== $stage: fresh database"
  prepare "$stage"
  if [[ $stage == indexed || $stage == partitioned ]]; then
    sql < sql/06_retention.sql > "results/$stage-retention.txt"
  fi

  echo "=== $stage: warm-up"
  bench warmup 500 "$read_url" "$write_url"
  for rate in $RATES; do
    echo "=== $stage: $rate reads/s"
    bench "$stage" "$rate" "$read_url" "$write_url" | tee -a results/summary.csv
  done

  if [[ $stage == pooled ]]; then
    echo "=== $stage: 300 client connections, max_connections = 100"
    POOL=300 bench direct-300 1000 "$DIRECT" "$DIRECT" | tee -a results/summary.csv || true
    POOL=300 bench pgbouncer-300 1000 "$BOUNCER" "$BOUNCER" | tee -a results/summary.csv
  fi
  if [[ $stage == replica ]]; then
    kill "$monitor"
  fi
done

column -s, -t results/summary.csv