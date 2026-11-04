// Open-loop step calibration for one service.
// Each step: constant arrival rate for STEP_S seconds, then GAP_S seconds idle so the
// backlog of an overloaded step drains before the next one. Every request is logged
// (k6 --out csv), percentiles are computed offline by analyze.py.
//
// env: SERVICE (teastore|hotel|thumbnailer|graph-bfs|imgrec), URL (http://<clusterIP>:8080/),
//      RATES (req/s per step, comma separated, decimals allowed), STEP_S, GAP_S
import http from 'k6/http';
import exec from 'k6/execution';

const SERVICE = __ENV.SERVICE;
const URL = __ENV.URL;
const RATES = __ENV.RATES.split(',').map(Number);
const STEP_S = Number(__ENV.STEP_S || 60);
const GAP_S = Number(__ENV.GAP_S || 20);
const B = 'sebs-benchmarks';
// ARRIVALS=poisson: iterations are scheduled at OVERSAMPLE x the rate, and each one sends a
// request with probability 1/OVERSAMPLE (Bernoulli thinning), which gives random, nearly
// Poisson arrivals at the same average rate. Default: evenly spaced arrivals.
const POISSON = (__ENV.ARRIVALS || 'even') === 'poisson';
const OVERSAMPLE = POISSON ? Number(__ENV.OVERSAMPLE || 10) : 1;

// SeBS sample inputs (same objects as loaded by load-data.yaml), rotated per request.
const THUMBS = [
  '0_action-adrenaline-adventure-1047051.jpg', '1_adult-adventure-back-view-2819546.jpg',
  '2_aerial-shot-architecture-bird-s-eye-view-2440013.jpg', '3_aerial-shot-architecture-bridge-2887493.jpg',
  '4_altitude-astrology-astronomy-1819650.jpg', '5_asphalt-atmosphere-cloudy-sky-2739010.jpg',
  '6_astronomy-desktop-wallpaper-evening-1624438.jpg', '7_beach-bird-s-eye-view-coast-2499700.jpg',
  '8_close-up-elder-elderly-2050990.jpg', '9_cobblestone-granite-pebbles-1029604.jpg',
];
const PHOTOS = [
  '512px-Cacatua_moluccensis_-Cincinnati_Zoo-8a.jpg', '782px-Pumiforme.JPG',
  '800px-20180630_Tesla_Model_S_70D_2015_midnight_blue_left_front.jpg', '800px-7weeks_old.JPG',
  '800px-Jammlich_crop.jpg', '800px-Porsche_991_silver_IAA.jpg', '800px-Sardinian_Warbler.jpg',
  '800px-Welsh_Springer_Spaniel.jpg',
];

const rnd = (lo, hi) => lo + Math.floor(Math.random() * (hi - lo + 1));   // inclusive

// TeaStore browsing mix through the web entry service: categories 2-6, 5 pages each,
// products 7-506 (checked on the deployed store).
function teastore() {
  const base = URL.replace(/\/$/, '') + '/tools.descartes.teastore.webui';
  const u = Math.random();
  if (u < 0.2) return base + '/';
  if (u < 0.6) return `${base}/category?category=${rnd(2, 6)}&page=${rnd(1, 5)}`;
  return `${base}/product?id=${rnd(7, 506)}`;
}

// DeathStarBench hotel reservation, mixed-workload_type_1 ratios: search 0.6, recommend 0.39,
// user 0.005, reserve 0.005, same random parameters as its wrk2 script. Usernames use the
// form the user service actually stores ("Cornell_" + hex of the number's characters).
function hexOf(s) { return s.split('').map(c => c.charCodeAt(0).toString(16)).join(''); }
function hotel() {
  const base = URL.replace(/\/$/, '');
  const inDay = rnd(9, 23), outDay = rnd(inDay + 1, 24);
  const d = (n) => `2015-04-${String(n).padStart(2, '0')}`;
  const lat = (38.0235 + (rnd(0, 481) - 240.5) / 1000.0).toFixed(4);
  const lon = (-122.095 + (rnd(0, 325) - 157.0) / 1000.0).toFixed(4);
  const id = String(rnd(0, 500));
  const user = `Cornell_${hexOf(id)}`, pass = id.repeat(10);
  const u = Math.random();
  if (u < 0.6) return `${base}/hotels?inDate=${d(inDay)}&outDate=${d(outDay)}&lat=${lat}&lon=${lon}`;
  if (u < 0.99) return `${base}/recommendations?require=${['dis', 'rate', 'price'][rnd(0, 2)]}&lat=${lat}&lon=${lon}`;
  if (u < 0.995) return `${base}/user?username=${user}&password=${pass}`;
  return `${base}/reservation?inDate=${d(inDay)}&outDate=${d(outDay)}&lat=${lat}&lon=${lon}` +
         `&hotelId=${rnd(1, 80)}&customerName=${user}&username=${user}&password=${pass}&number=1`;
}

function event(i) {
  // since 2026-10-01 (docs/decisions.md): thumbnailer on SeBS's 800 px photos, graph-bfs input "small";
  // compression and image recognition were removed from the study
  if (SERVICE === 'thumbnailer') {
    return { object: { key: PHOTOS[i % PHOTOS.length], width: 200, height: 200 },
             bucket: { bucket: B, input: '411.image-recognition-1-input', output: '210.thumbnailer-0-output' } };
  }
  if (SERVICE === 'graph-bfs') return { size: 10000, seed: 42 };
  if (SERVICE === 'imgrec') {   // new-service shift (2026-10-28)
    return { object: { model: 'resnet50-19c8e357.pth', input: PHOTOS[i % PHOTOS.length] },
             bucket: { bucket: B, model: '411.image-recognition-0-input', input: '411.image-recognition-1-input' } };
  }
  throw new Error(`unknown SERVICE ${SERVICE}`);
}

// Warm-up (tagged step "warm", ignored by analyze.py): a new replica's first request
// pays the cold start (imgrec loads its model), which calibration excludes.
const REPLICAS = Number(__ENV.REPLICAS || 1);
const WARM_S = Number(__ENV.WARM_S || 20);
const scenarios = {
  warm: {
    // WARM_RATE (req/s) warms a service at a real load (TeaStore's JIT needs minutes);
    // default: one request per replica every 2 s, enough to load imgrec's model.
    executor: 'constant-arrival-rate',
    rate: __ENV.WARM_RATE ? Math.round(Number(__ENV.WARM_RATE) * 10) : REPLICAS,
    timeUnit: __ENV.WARM_RATE ? '10s' : '2s', duration: `${WARM_S}s`,
    preAllocatedVUs: 20, maxVUs: 500, tags: { step: 'warm', rate: '0' }, gracefulStop: '30s',
  },
};

// One constant-arrival-rate scenario per step. Rates below 1 req/s use a longer time unit.
RATES.forEach((r, i) => {
  scenarios[`step${i}`] = {
    executor: 'constant-arrival-rate',
    rate: Math.round(r * 10 * OVERSAMPLE), timeUnit: '10s',
    duration: `${STEP_S}s`, startTime: `${WARM_S + GAP_S + i * (STEP_S + GAP_S)}s`,
    // VUs = concurrent requests in flight. Capped for high rates; requests k6 cannot start
    // in time show up as dropped_iterations (counted by analyze.py as overload).
    preAllocatedVUs: Math.min(500, Math.max(20, Math.ceil(r * 5))),
    maxVUs: Math.min(3000, Math.max(100, Math.ceil(r * 60))),
    tags: { step: String(i), rate: String(r), os: String(OVERSAMPLE) },
    gracefulStop: '30s',
  };
});

export const options = { scenarios, discardResponseBodies: true, summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)'] };

export default function () {
  if (POISSON && exec.scenario.name !== 'warm' && Math.random() >= 1 / OVERSAMPLE) return;
  if (SERVICE === 'teastore') return http.get(teastore(), { timeout: '30s' });
  if (SERVICE === 'hotel') return http.get(hotel(), { timeout: '30s' });
  const body = JSON.stringify(event(__ITER));
  http.post(URL, body, { headers: { 'Content-Type': 'application/json' }, timeout: '30s' });
}
