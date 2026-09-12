WhatYouAre Handing In
01 Three protocol runs Same endpoint, same arrival rate, same duration, over HTTP/1.1, HTTP/2 and HTTP/3. Report
p50, p95, p99 and achieved RPS for each, plus the negotiated version proved by curl.
02 The cost of the handshake Repeat one protocol with connection reuse on and off. Name the handshake cost in
milliseconds and show which metric you took it from.
03 The same, on a bad
network
Add 100 ms delay and 1 % loss with netem and repeat the three protocol runs. State which
version degrades least and by how much.
04 Three explanations — the
actual work
Two or three sentences per result, each naming a mechanism from the lecture: handshake
round trips, initial congestion window, multiplexing, head-of-line blocking, HPACK. “Newer is
faster” earns nothing.
Deliverable: a repository with the service, the Caddyfile, the k6 script and the netem commands, plus REPORT.md. If you could not get
HTTP/3 load generation working, say so explicitly and report the curl comparison instead — an honest limitation costs nothing.


k6—FixedArrivalRate,Not Fixed Users
LOAD.JS
import http from 'k6/http';
export const options = {
 insecureSkipTLSVerify: true,
 noConnectionReuse: __ENV.COLD === '1', // the cold run
 summaryTrendStats: ['p(50)','p(95)','p(99)','max'],
 scenarios: {
 steady: {
 executor: 'constant-arrival-rate',
 rate: 500, timeUnit: '1s', duration: '60s',
 preAllocatedVUs: 200, maxVUs: 800,
 },
 },
};
export default function () {
 http.get(`${__ENV.URL}/api/quote/1`);
}
Two runs from one script
COLD=1 turns off connection reuse. Same script, same
load, one variable changed — that is the handshake
experiment.
k6 covers 1.1 and 2
It negotiates HTTP/2 over TLS automatically; force 1.1 by
pointing at an endpoint that only offers http/1.1 in
ALPN. Verify with the http_req_duration tag for
protocol.
HTTP/3 needs another tool
Stock k6 does not generate h3. Use h2load built with
QUIC support, or a scripted curl loop. If you cannot get
either working, report the single-request curl comparison
and state the limitation.