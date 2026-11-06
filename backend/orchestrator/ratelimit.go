package main

import (
	"crypto/subtle"
	"encoding/json"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// slidingLimiter allows at most max events per window for each key.
type slidingLimiter struct {
	mu     sync.Mutex
	max    int
	window time.Duration
	hits   map[string][]time.Time
}

func newSlidingLimiter(max int, window time.Duration) *slidingLimiter {
	l := &slidingLimiter{max: max, window: window, hits: map[string][]time.Time{}}
	go func() { // drop idle keys so the map cannot grow without bound
		for range time.Tick(window) {
			l.gc()
		}
	}()
	return l
}

// Allow records an event for key and reports whether it is within the limit.
// When denied it returns how long until the oldest event expires.
func (l *slidingLimiter) Allow(key string) (bool, time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	cutoff := now.Add(-l.window)
	h := l.hits[key][:0]
	for _, t := range l.hits[key] {
		if t.After(cutoff) {
			h = append(h, t)
		}
	}
	if len(h) >= l.max {
		l.hits[key] = h
		return false, h[0].Add(l.window).Sub(now)
	}
	l.hits[key] = append(h, now)
	return true, 0
}

func (l *slidingLimiter) gc() {
	l.mu.Lock()
	defer l.mu.Unlock()
	cutoff := time.Now().Add(-l.window)
	for k, h := range l.hits {
		if len(h) == 0 || !h[len(h)-1].After(cutoff) {
			delete(l.hits, k)
		}
	}
}

// failureLockout counts failures per key and locks the key out once it reaches
// max failures inside window. Success clears the counter.
type failureLockout struct {
	mu       sync.Mutex
	max      int
	window   time.Duration
	lockFor  time.Duration
	failures map[string][]time.Time
	locked   map[string]time.Time
}

func newFailureLockout(max int, window, lockFor time.Duration) *failureLockout {
	f := &failureLockout{max: max, window: window, lockFor: lockFor,
		failures: map[string][]time.Time{}, locked: map[string]time.Time{}}
	go func() {
		for range time.Tick(window) {
			f.gc()
		}
	}()
	return f
}

// Locked reports whether key is currently locked out and for how long.
func (f *failureLockout) Locked(key string) (bool, time.Duration) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if until, ok := f.locked[key]; ok {
		if d := time.Until(until); d > 0 {
			return true, d
		}
		delete(f.locked, key)
		delete(f.failures, key)
	}
	return false, 0
}

// Fail records a failure; it returns true if this failure triggered a lockout.
func (f *failureLockout) Fail(key string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	now := time.Now()
	cutoff := now.Add(-f.window)
	h := f.failures[key][:0]
	for _, t := range f.failures[key] {
		if t.After(cutoff) {
			h = append(h, t)
		}
	}
	h = append(h, now)
	f.failures[key] = h
	if len(h) >= f.max {
		f.locked[key] = now.Add(f.lockFor)
		return true
	}
	return false
}

func (f *failureLockout) Reset(key string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.failures, key)
	delete(f.locked, key)
}

func (f *failureLockout) gc() {
	f.mu.Lock()
	defer f.mu.Unlock()
	now := time.Now()
	for k, until := range f.locked {
		if now.After(until) {
			delete(f.locked, k)
			delete(f.failures, k)
		}
	}
	cutoff := now.Add(-f.window)
	for k, h := range f.failures {
		if len(h) == 0 || !h[len(h)-1].After(cutoff) {
			delete(f.failures, k)
		}
	}
}

// --- limits in force -------------------------------------------------------

var (
	// Brute force: lock an account (or IP) after repeated failures.
	loginAccountLock = newFailureLockout(5, 15*time.Minute, 15*time.Minute)
	loginIPLock      = newFailureLockout(20, 15*time.Minute, 15*time.Minute)
	adminKeyLock     = newFailureLockout(10, 15*time.Minute, 15*time.Minute)

	// Spam: cap request rates regardless of outcome.
	loginAttemptLimit = newSlidingLimiter(30, time.Minute)
	signupLimit       = newSlidingLimiter(5, time.Hour)
	signupGlobalLimit = newSlidingLimiter(60, time.Hour) // all sources combined
	submitIPLimit     = newSlidingLimiter(60, time.Minute)
	generalIPLimit    = newSlidingLimiter(900, time.Minute)
)

// Request body caps.
const (
	maxAuthBody   = 16 << 10  // 16 KiB: login/signup JSON
	maxSubmitBody = 100 << 20 // 100 MiB: image uploads arrive inline
)

var trustedProxyNets = mustCIDRs("127.0.0.0/8", "::1/128", "172.16.0.0/12", "10.0.0.0/8")

func mustCIDRs(cidrs ...string) []*net.IPNet {
	var out []*net.IPNet
	for _, c := range cidrs {
		if _, n, err := net.ParseCIDR(c); err == nil {
			out = append(out, n)
		}
	}
	return out
}

func isTrustedProxy(ip net.IP) bool {
	for _, n := range trustedProxyNets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

// clientIP returns the caller's address. X-Forwarded-For / X-Real-IP are honoured
// only when the direct peer is a trusted proxy (loopback or docker/private
// networks), so a LAN client cannot spoof its way around per-IP limits.
func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	peer := net.ParseIP(host)
	if peer != nil && isTrustedProxy(peer) {
		if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
			// Right-most entry added by our trusted proxy chain is the most reliable;
			// the left-most is what the original client claimed. Use the left-most
			// valid IP (nginx appends, UIs forward it as-is).
			first := strings.TrimSpace(strings.Split(xff, ",")[0])
			if ip := net.ParseIP(first); ip != nil {
				return ip.String()
			}
		}
		if xr := net.ParseIP(strings.TrimSpace(r.Header.Get("X-Real-IP"))); xr != nil {
			return xr.String()
		}
	}
	return host
}

// tooManyRequests writes a 429 with Retry-After.
func tooManyRequests(w http.ResponseWriter, msg string, wait time.Duration) {
	secs := int(wait.Seconds()) + 1
	if secs < 1 {
		secs = 1
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Retry-After", strconv.Itoa(secs))
	w.WriteHeader(http.StatusTooManyRequests)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"error": msg, "retry_after_seconds": secs,
	})
}

// secureEqual compares secrets in constant time.
func secureEqual(a, b string) bool {
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

// generalRateLimit wraps the whole mux with a generous per-IP ceiling so a single
// source cannot flood any endpoint (status polling included).
func generalRateLimit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodOptions {
			next.ServeHTTP(w, r)
			return
		}
		if ok, wait := generalIPLimit.Allow(clientIP(r)); !ok {
			tooManyRequests(w, "too many requests", wait)
			return
		}
		next.ServeHTTP(w, r)
	})
}
