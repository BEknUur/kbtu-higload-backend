import http from 'k6/http';

export const options = {
    insecureSkipTLSVerify: true,

    noConnectionReuse: __ENV.COLD === '1',

    summaryTrendStats: [
        'p(50)',
        'p(95)',
        'p(99)',
        'max',
    ],

    scenarios: {
        steady: {
            executor: 'constant-arrival-rate',
            rate: 500,
            timeUnit: '1s',
            duration: '60s',
            preAllocatedVUs: 200,
            maxVUs: 800,
        },
    },
};

export default function () {
    http.get(`${__ENV.URL}/checklist`);
}