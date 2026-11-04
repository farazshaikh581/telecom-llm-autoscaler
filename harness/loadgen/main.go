// Open-loop trace replayer for the five user-facing entries (TeaStore, three SeBS functions,
// hotel frontend). Replaces k6 for the main runs: k6 logs about 13 rows per request, and a
// 75-min run sends about 11 million requests.
//
// Arrivals: -arrivals even gives evenly spaced requests instead (no-queue baselines).
// By default, a Poisson process per entry whose rate follows the trace, second by second
// (piecewise constant). Arrival times and request contents come from a fixed seed, so every
// controller faces exactly the same requests.
//
// Connections: kube-proxy balances per connection, so idle connections are closed every second;
// a new copy gets traffic within about a second of becoming Ready.
//
// Latency: from getting a connection to reading the whole response (as k6's http_req_duration,
// which leaves out connection setup). Also logged: the delay from the scheduled time to send.
//
// Warm-up ramp (-ramp): rates start at 20% of the trace and reach it after the given seconds, so
// fresh copies (and TeaStore's Java code) warm up before full load. Same for every controller.
//
// Output: one gzip CSV line per request (entry, scheduled ms, latency us, send delay us, status,
// error). Live stats for controllers: GET /stats?window=60 on the listen address.
package main

import (
	"bufio"
	"compress/gzip"
	"context"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"math/rand"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

var entries = []string{"teastore", "thumbnailer", "graph-bfs", "hotel", "imgrec"}   // 2026-10-01: edge latency class (docs/decisions.md); imgrec: the new-service shift (2026-10-28), last so the other entries keep their seeds

// ---------- request builders (same mixes as tools/calibration/calib.js) ----------

type builder struct {
	rnd  *rand.Rand
	base string
	iter int
}

func (b *builder) between(lo, hi int) int { return lo + b.rnd.Intn(hi-lo+1) }

var thumbs = []string{
	"0_action-adrenaline-adventure-1047051.jpg", "1_adult-adventure-back-view-2819546.jpg",
	"2_aerial-shot-architecture-bird-s-eye-view-2440013.jpg", "3_aerial-shot-architecture-bridge-2887493.jpg",
	"4_altitude-astrology-astronomy-1819650.jpg", "5_asphalt-atmosphere-cloudy-sky-2739010.jpg",
	"6_astronomy-desktop-wallpaper-evening-1624438.jpg", "7_beach-bird-s-eye-view-coast-2499700.jpg",
	"8_close-up-elder-elderly-2050990.jpg", "9_cobblestone-granite-pebbles-1029604.jpg",
}
var photos = []string{
	"512px-Cacatua_moluccensis_-Cincinnati_Zoo-8a.jpg", "782px-Pumiforme.JPG",
	"800px-20180630_Tesla_Model_S_70D_2015_midnight_blue_left_front.jpg", "800px-7weeks_old.JPG",
	"800px-Jammlich_crop.jpg", "800px-Porsche_991_silver_IAA.jpg", "800px-Sardinian_Warbler.jpg",
	"800px-Welsh_Springer_Spaniel.jpg",
}

const bucket = "sebs-benchmarks"

type req struct {
	method string
	url    string
	body   []byte
}

// TeaStore browsing mix: home 0.2, category 0.4, product 0.4.
func (b *builder) teastore() req {
	base := strings.TrimRight(b.base, "/") + "/tools.descartes.teastore.webui"
	u := b.rnd.Float64()
	switch {
	case u < 0.2:
		return req{"GET", base + "/", nil}
	case u < 0.6:
		return req{"GET", fmt.Sprintf("%s/category?category=%d&page=%d", base, b.between(2, 6), b.between(1, 5)), nil}
	}
	return req{"GET", fmt.Sprintf("%s/product?id=%d", base, b.between(7, 506)), nil}
}

func hexOf(s string) string {
	var sb strings.Builder
	for _, c := range s {
		sb.WriteString(strconv.FormatInt(int64(c), 16))
	}
	return sb.String()
}

// DeathStarBench hotel reservation, mixed-workload_type_1: search 0.6, recommend 0.39, user 0.005, reserve 0.005.
func (b *builder) hotel() req {
	base := strings.TrimRight(b.base, "/")
	in := b.between(9, 23)
	out := b.between(in+1, 24)
	d := func(n int) string { return fmt.Sprintf("2015-04-%02d", n) }
	lat := fmt.Sprintf("%.4f", 38.0235+(float64(b.between(0, 481))-240.5)/1000.0)
	lon := fmt.Sprintf("%.4f", -122.095+(float64(b.between(0, 325))-157.0)/1000.0)
	id := strconv.Itoa(b.between(0, 500))
	user := "Cornell_" + hexOf(id)
	pass := strings.Repeat(id, 10)
	q := url.Values{}
	u := b.rnd.Float64()
	var path string
	switch {
	case u < 0.6:
		path = "/hotels"
		q.Set("inDate", d(in)); q.Set("outDate", d(out)); q.Set("lat", lat); q.Set("lon", lon)
	case u < 0.99:
		path = "/recommendations"
		q.Set("require", []string{"dis", "rate", "price"}[b.between(0, 2)]); q.Set("lat", lat); q.Set("lon", lon)
	case u < 0.995:
		path = "/user"
		q.Set("username", user); q.Set("password", pass)
	default:
		path = "/reservation"
		q.Set("inDate", d(in)); q.Set("outDate", d(out)); q.Set("lat", lat); q.Set("lon", lon)
		q.Set("hotelId", strconv.Itoa(b.between(1, 80))); q.Set("customerName", user)
		q.Set("username", user); q.Set("password", pass); q.Set("number", "1")
	}
	return req{"GET", base + path + "?" + q.Encode(), nil}
}

func (b *builder) sebs(entry string) req {
	i := b.iter
	b.iter++
	var ev map[string]any
	switch entry {
	case "thumbnailer":   // SeBS's 800 px photos (60-240 KB) since 2026-10-01; the 0.8-4 MB set is in thumbs
		ev = map[string]any{"object": map[string]any{"key": photos[i%len(photos)], "width": 200, "height": 200},
			"bucket": map[string]any{"bucket": bucket, "input": "411.image-recognition-1-input", "output": "210.thumbnailer-0-output"}}
	case "graph-bfs":     // SeBS 503.graph-bfs input "small", as its input.py generates it
		ev = map[string]any{"size": 10000, "seed": 42}
	case "imgrec":        // SeBS 411.image-recognition (ResNet-50), as before 2026-10-01
		ev = map[string]any{"object": map[string]any{"model": "resnet50-19c8e357.pth", "input": photos[i%len(photos)]},
			"bucket": map[string]any{"bucket": bucket, "model": "411.image-recognition-0-input", "input": "411.image-recognition-1-input"}}
	}
	body, _ := json.Marshal(ev)
	return req{"POST", b.base, body}
}

func (b *builder) next(entry string) req {
	switch entry {
	case "teastore":
		return b.teastore()
	case "hotel":
		return b.hotel()
	}
	return b.sebs(entry)
}

// ---------- live statistics ----------

const keepSeconds = 600

type second struct {
	sent, done, ok, failed, dropped int
	lat                             []float64 // ms, successful requests completed in this second
}

type stats struct {
	mu       sync.Mutex
	secs     map[int]*second // by second since start, completion time for done/ok/failed/lat, send time for sent/dropped
	inflight int64
}

func (s *stats) at(sec int) *second {
	x := s.secs[sec]
	if x == nil {
		x = &second{}
		s.secs[sec] = x
		delete(s.secs, sec-keepSeconds)
	}
	return x
}

func pct(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return math.NaN()
	}
	k := float64(len(sorted)-1) * p / 100
	lo := int(k)
	hi := lo + 1
	if hi >= len(sorted) {
		hi = len(sorted) - 1
	}
	return sorted[lo] + (sorted[hi]-sorted[lo])*(k-float64(lo))
}

// ---------- main ----------

type record struct {
	entry                  int
	schedMs                float64
	latUs, delayUs, status int64
	err                    string
}

func loadTrace(path string) (map[string][]float64, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var r io.Reader = f
	if strings.HasSuffix(path, ".gz") {
		gz, err := gzip.NewReader(f)
		if err != nil {
			return nil, err
		}
		r = gz
	}
	rows, err := csv.NewReader(r).ReadAll()
	if err != nil {
		return nil, err
	}
	col := map[string]int{}
	for i, h := range rows[0] {
		col[h] = i
	}
	out := map[string][]float64{}
	for _, e := range entries {
		i, ok := col[e]
		if !ok {
			continue
		}
		for _, row := range rows[1:] {
			v, _ := strconv.ParseFloat(row[i], 64)
			out[e] = append(out[e], v)
		}
	}
	return out, nil
}

func main() {
	tracePath := flag.String("trace", "", "per-second rates (csv or csv.gz): second,<entry>...")
	outPath := flag.String("out", "requests.csv.gz", "per-request log")
	listen := flag.String("listen", "127.0.0.1:9901", "live stats address")
	targetsArg := flag.String("targets", "", "entry=url,... (entries without a target are not sent)")
	scale := flag.Float64("scale", 1.0, "multiply every rate")
	seed := flag.Int64("seed", 1, "arrival and content seed (same seed = same requests)")
	maxInflight := flag.Int("max-inflight", 3000, "per entry; arrivals beyond it are dropped and counted")
	timeout := flag.Duration("timeout", 30*time.Second, "request timeout")
	duration := flag.Int("duration", 0, "stop after this many seconds (0 = whole trace)")
	arrivals := flag.String("arrivals", "poisson", "poisson | even (evenly spaced, for no-queue baselines)")
	ramp := flag.Int("ramp", 0, "seconds: rates ramp from 20% to 100% of the trace over this start period")
	recycle := flag.Duration("recycle", time.Second, "close idle connections this often (0 = never)")
	flag.Parse()

	trace, err := loadTrace(*tracePath)
	if err != nil {
		log.Fatal(err)
	}
	targets := map[string]string{}
	for _, kv := range strings.Split(*targetsArg, ",") {
		if p := strings.SplitN(kv, "=", 2); len(p) == 2 {
			targets[p[0]] = p[1]
		}
	}

	tr := &http.Transport{MaxIdleConns: 20000, MaxIdleConnsPerHost: 5000, IdleConnTimeout: 90 * time.Second,
		DisableCompression: true}
	client := &http.Client{Transport: tr, Timeout: *timeout}

	outF, err := os.Create(*outPath)
	if err != nil {
		log.Fatal(err)
	}
	gz := gzip.NewWriter(outF)
	bw := bufio.NewWriterSize(gz, 1<<20)
	fmt.Fprintln(bw, "entry,sched_ms,lat_us,send_delay_us,status,error")
	recs := make(chan record, 200000)
	var writerDone sync.WaitGroup
	writerDone.Add(1)
	go func() {
		defer writerDone.Done()
		for r := range recs {
			fmt.Fprintf(bw, "%s,%.1f,%d,%d,%d,%s\n", entries[r.entry], r.schedMs, r.latUs, r.delayUs, r.status, r.err)
		}
	}()

	rateAt := func(e string, sec int) float64 {
		r := trace[e][sec] * *scale
		if sec < *ramp {
			r *= 0.2 + 0.8*float64(sec)/float64(*ramp)
		}
		return r
	}

	st := map[string]*stats{}
	for _, e := range entries {
		st[e] = &stats{secs: map[int]*second{}}
	}
	start := time.Now()
	nowSec := func() float64 { return time.Since(start).Seconds() }

	// live stats: window over the last N whole seconds
	http.HandleFunc("/stats", func(w http.ResponseWriter, r *http.Request) {
		win, _ := strconv.Atoi(r.URL.Query().Get("window"))
		if win <= 0 {
			win = 60
		}
		now := nowSec()
		end := int(now) // seconds [end-win, end) are complete
		out := map[string]any{"elapsed_s": now, "window_s": win}
		es := map[string]any{}
		for _, e := range entries {
			if targets[e] == "" {
				continue
			}
			s := st[e]
			s.mu.Lock()
			var sent, done, ok, failed, dropped int
			var lat []float64
			for sec := end - win; sec < end; sec++ {
				if x := s.secs[sec]; x != nil {
					sent += x.sent; done += x.done; ok += x.ok; failed += x.failed; dropped += x.dropped
					lat = append(lat, x.lat...)
				}
			}
			s.mu.Unlock()
			offered := 0.0
			for sec := end - win; sec < end; sec++ {
				if sec >= 0 && sec < len(trace[e]) {
					offered += rateAt(e, sec)
				}
			}
			sort.Float64s(lat)
			sum := 0.0
			for _, v := range lat {
				sum += v
			}
			l := map[string]any{"n": len(lat)}
			if len(lat) > 0 {
				l["avg"] = sum / float64(len(lat))
				for _, p := range []float64{50, 75, 90, 95, 99} {
					l[fmt.Sprintf("p%g", p)] = pct(lat, p)
				}
				l["max"] = lat[len(lat)-1]
			}
			fw := float64(win)
			es[e] = map[string]any{"target_rps": offered / fw, "sent_rps": float64(sent) / fw,
				"done_rps": float64(done) / fw, "ok_rps": float64(ok) / fw, "failed_rps": float64(failed) / fw,
				"dropped_rps": float64(dropped) / fw, "inflight": atomic.LoadInt64(&s.inflight), "latency_ms": l}
		}
		out["entries"] = es
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(out)
	})
	srv := &http.Server{Addr: *listen}
	go func() {
		if err := srv.ListenAndServe(); err != http.ErrServerClosed {
			log.Fatal(err)
		}
	}()

	// recycle idle connections so new copies receive traffic
	if *recycle > 0 {
		go func() {
			for range time.Tick(*recycle) {
				tr.CloseIdleConnections()
			}
		}()
	}

	ctx, cancel := context.WithCancel(context.Background())
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() { <-sig; log.Print("signal: stopping"); cancel() }()

	var reqs sync.WaitGroup
	var sched sync.WaitGroup
	for ei, e := range entries {
		if targets[e] == "" || len(trace[e]) == 0 {
			continue
		}
		sched.Add(1)
		go func(ei int, e string) {
			defer sched.Done()
			rates := trace[e]
			n := len(rates)
			if *duration > 0 && *duration < n {
				n = *duration
			}
			arr := rand.New(rand.NewSource(*seed*1000 + int64(ei)))       // arrival times
			b := &builder{rnd: rand.New(rand.NewSource(*seed*1000 + 100 + int64(ei))), base: targets[e]}
			s := st[e]
			t := 0.0 // seconds since start, next arrival
			for {
				sec := int(t)
				if sec >= n {
					return
				}
				r := rateAt(e, sec)
				if r <= 0 {
					t = float64(sec + 1)
					continue
				}
				if *arrivals == "even" {
					t += 1 / r // evenly spaced at the current second's rate
				} else {
					gap := arr.ExpFloat64() / r
					if t+gap >= float64(sec+1) { // memoryless: restart at the boundary with the next second's rate
						t = float64(sec + 1)
						continue
					}
					t += gap
				}
				rq := b.next(e) // built in arrival order, so contents do not depend on timing
				wait := time.Until(start.Add(time.Duration(t * float64(time.Second))))
				if wait > 0 {
					select {
					case <-time.After(wait):
					case <-ctx.Done():
						return
					}
				} else if ctx.Err() != nil {
					return
				}
				s.mu.Lock()
				if s.inflight >= int64(*maxInflight) {
					s.at(sec).dropped++
					s.mu.Unlock()
					recs <- record{entry: ei, schedMs: t * 1000, status: 0, err: "dropped"}
					continue
				}
				s.inflight++
				s.at(sec).sent++
				s.mu.Unlock()
				reqs.Add(1)
				go func(t float64, rq req) {
					defer reqs.Done()
					sendAt := time.Now()
					delay := sendAt.Sub(start.Add(time.Duration(t * float64(time.Second))))
					var gotConn time.Time
					trace := &httptrace.ClientTrace{GotConn: func(httptrace.GotConnInfo) { gotConn = time.Now() }}
					var body io.Reader
					if rq.body != nil {
						body = strings.NewReader(string(rq.body))
					}
					hr, _ := http.NewRequestWithContext(httptrace.WithClientTrace(context.Background(), trace), rq.method, rq.url, body)
					if rq.body != nil {
						hr.Header.Set("Content-Type", "application/json")
					}
					status, errs := int64(0), ""
					resp, err := client.Do(hr)
					if err == nil {
						_, err = io.Copy(io.Discard, resp.Body)
						resp.Body.Close()
						status = int64(resp.StatusCode)
					}
					end := time.Now()
					if err != nil {
						errs = "timeout"
						if !strings.Contains(err.Error(), "Timeout") && !strings.Contains(err.Error(), "deadline") {
							errs = "conn"
						}
					}
					if gotConn.IsZero() {
						gotConn = sendAt
					}
					latMs := float64(end.Sub(gotConn).Microseconds()) / 1000
					csec := int(end.Sub(start).Seconds())
					s.mu.Lock()
					s.inflight--
					x := s.at(csec)
					x.done++
					if status == 200 {
						x.ok++
						x.lat = append(x.lat, latMs)
					} else {
						x.failed++
					}
					s.mu.Unlock()
					recs <- record{entry: ei, schedMs: t * 1000, latUs: end.Sub(gotConn).Microseconds(),
						delayUs: delay.Microseconds(), status: status, err: errs}
				}(t, rq)
			}
		}(ei, e)
	}
	sched.Wait()
	log.Printf("schedule finished at %.1f s; waiting for requests in flight", nowSec())
	reqs.Wait()
	close(recs)
	writerDone.Wait()
	bw.Flush()
	gz.Close()
	outF.Close()
	srv.Close()
	log.Printf("done at %.1f s", nowSec())
}
