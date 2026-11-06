package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func decide(in stateInputs, mut func(*GPUState)) GPUState {
	st := GPUState{FreeVRAMGB: 20, TotalVRAMGB: 24}
	if mut != nil {
		mut(&st)
	}
	decideState(&st, in)
	return st
}

func TestDecideState(t *testing.T) {
	cases := []struct {
		name      string
		in        stateInputs
		mut       func(*GPUState)
		state     string
		available bool
	}{
		{"idle gpu is free", stateInputs{}, nil, GPUStateFree, true},
		{"low util is not external", stateInputs{util: 5}, nil, GPUStateFree, true},
		{"foreign load blocks", stateInputs{util: 98}, func(s *GPUState) { s.ExternalVRAMGB = 8 }, GPUStateExternal, false},
		{"warm worker allows", stateInputs{warm: true}, nil, GPUStateWarm, true},
		{"processing blocks", stateInputs{processing: true, util: 100}, nil, GPUStateBusy, false},
		{"loading blocks", stateInputs{loading: true}, nil, GPUStateLoading, false},
		{"queued job blocks", stateInputs{hasJob: true, jobStatus: "QUEUED"}, nil, GPUStateBusy, false},
		{"our own load is never external", stateInputs{processing: true, util: 100}, nil, GPUStateBusy, false},
		{"held vram alone is not busy", stateInputs{util: 0}, func(s *GPUState) { s.ExternalVRAMGB = 12 }, GPUStateFree, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			st := decide(c.in, c.mut)
			if st.State != c.state || st.IsAvailable != c.available {
				t.Fatalf("got state=%s available=%v, want %s/%v (%s)", st.State, st.IsAvailable, c.state, c.available, st.Message)
			}
			if !st.IsAvailable && st.RetryAfterSec == 0 {
				t.Fatalf("blocked state must carry retry_after")
			}
		})
	}
}

func TestVRAMAllows(t *testing.T) {
	app := AppConfig{Name: "Big", GPUVramGB: 16}
	if ok, _ := vramAllows(app, GPUState{State: GPUStateFree, FreeVRAMGB: 10.5, ExternalVRAMGB: 11.5}); ok {
		t.Fatal("16GB model must not fit in 10.5GB free")
	}
	if ok, _ := vramAllows(app, GPUState{State: GPUStateFree, FreeVRAMGB: 22}); !ok {
		t.Fatal("16GB model should fit in 22GB free")
	}
	// warm worker's VRAM is reclaimable
	if ok, _ := vramAllows(app, GPUState{State: GPUStateWarm, FreeVRAMGB: 8, OrchVRAMGB: 12}); !ok {
		t.Fatal("warm worker VRAM should count as reclaimable")
	}
	if ok, _ := vramAllows(AppConfig{GPUVramGB: 0}, GPUState{FreeVRAMGB: 0}); !ok {
		t.Fatal("apps without a VRAM requirement are never blocked on VRAM")
	}
}

func TestRateLimitPerKey(t *testing.T) {
	for i := 0; i < 20; i++ {
		if !checkRateLimit("test-a") {
			t.Fatalf("request %d should pass", i)
		}
	}
	if checkRateLimit("test-a") {
		t.Fatal("21st request must be limited")
	}
	if !checkRateLimit("test-b") {
		t.Fatal("other key must be unaffected")
	}
}

func TestFailureLockout(t *testing.T) {
	f := newFailureLockout(3, time.Minute, time.Minute)
	for i := 0; i < 2; i++ {
		if f.Fail("a") {
			t.Fatal("should not lock before max")
		}
	}
	if !f.Fail("a") {
		t.Fatal("third failure must lock")
	}
	if locked, _ := f.Locked("a"); !locked {
		t.Fatal("a must be locked")
	}
	if locked, _ := f.Locked("b"); locked {
		t.Fatal("b must be unaffected")
	}
	f.Reset("a")
	if locked, _ := f.Locked("a"); locked {
		t.Fatal("reset must unlock")
	}
}

func TestSlidingLimiter(t *testing.T) {
	l := newSlidingLimiter(2, time.Minute)
	l.Allow("x")
	l.Allow("x")
	if ok, wait := l.Allow("x"); ok || wait <= 0 {
		t.Fatal("third event must be denied with a wait")
	}
}

func TestClientIPSpoofing(t *testing.T) {
	mk := func(remote, xff string) *http.Request {
		r := httptest.NewRequest("GET", "/", nil)
		r.RemoteAddr = remote
		if xff != "" {
			r.Header.Set("X-Forwarded-For", xff)
		}
		return r
	}
	if got := clientIP(mk("172.18.0.5:1234", "203.0.113.9")); got != "203.0.113.9" {
		t.Fatalf("trusted proxy XFF should be honoured, got %s", got)
	}
	if got := clientIP(mk("192.168.50.77:1234", "1.2.3.4")); got != "192.168.50.77" {
		t.Fatalf("LAN client must not spoof XFF, got %s", got)
	}
}
