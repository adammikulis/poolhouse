package tail

import (
	"strings"
	"testing"
)

func TestTheThreeServicesAndWhoReachesThem(t *testing.T) {
	want := map[string]service{"pool": {7447, sameUser}, "phone": {7450, sameUser}, "guest": {7453, anyCaller}}
	if len(services) != len(want) {
		t.Fatalf("the node carries %d services, not %d", len(services), len(want))
	}
	for name, expected := range want {
		if services[name] != expected {
			t.Errorf("%s is %+v, not %+v", name, services[name], expected)
		}
	}
}

func TestOnlyTheSameUserReachesAnOwnService(t *testing.T) {
	me := identity{user: 7}
	for _, c := range []struct {
		name         string
		who          audience
		self, caller identity
		want         bool
	}{
		{"my other device", sameUser, me, identity{user: 7}, true},
		{"a friend it is shared with", sameUser, me, identity{user: 8}, false},
		{"a tagged caller", sameUser, me, identity{user: 7, tagged: true}, false},
		{"a tagged node and a tagged caller", sameUser, identity{user: 7, tagged: true}, identity{user: 7, tagged: true}, false},
		{"nobody signed in", sameUser, identity{}, identity{}, false},
		{"a friend at the guest port", anyCaller, me, identity{user: 8}, true},
	} {
		if got := admits(c.who, c.self, c.caller); got != c.want {
			t.Errorf("%s: admitted %v, not %v", c.name, got, c.want)
		}
	}
}

func TestAServiceIsForwardedToLoopbackOnly(t *testing.T) {
	for _, good := range []string{"127.0.0.1:7447", "[::1]:7453"} {
		if err := checkTarget(good); err != nil {
			t.Errorf("%s was refused: %v", good, err)
		}
	}
	for _, bad := range []string{"0.0.0.0:7447", "192.168.1.5:7447", "100.64.0.1:7447", "localhost:7447", "127.0.0.1:0", ":7447", ""} {
		if checkTarget(bad) == nil {
			t.Errorf("%q was accepted as a target", bad)
		}
	}
}

func TestOnlyATailnetServiceIsDialed(t *testing.T) {
	for _, good := range []string{"100.64.0.1:7447", "100.127.255.254:7453", "[fd7a:115c:a1e0::1]:7450", "poolhouse-desk.tail1234.ts.net:7453",
		"POOLHOUSE-DESK.tail1234.ts.net.:7453"} {
		if err := checkDial(good); err != nil {
			t.Errorf("%s was refused: %v", good, err)
		}
	}
	for _, bad := range []string{"100.64.0.1:22", "100.64.0.1:8770", "192.168.1.5:7447", "100.128.0.1:7447", "8.8.8.8:7447", "127.0.0.1:7447",
		"[::1]:7447", "example.com:7447", "ts.net.example.com:7447", "-a.tail1.ts.net:7447", "a..ts.net:7447", "a_b.ts.net:7447", "100.64.0.1",
		""} {
		if checkDial(bad) == nil {
			t.Errorf("%q would be dialed", bad)
		}
	}
}

func TestTheMachineNameStartsWithPoolhouse(t *testing.T) {
	for device, want := range map[string]string{
		"desk MAC":    "poolhouse-desk-mac",
		"  lab_pc!! ": "poolhouse-lab-pc",
		"":            "poolhouse",
		"éé":          "poolhouse",
	} {
		if got := hostName(device); got != want {
			t.Errorf("%q gives %q, not %q", device, got, want)
		}
	}
	long := hostName(strings.Repeat("a", 52) + " " + strings.Repeat("b", 40))
	if len(long) > 63 || !dnsLabel(long) {
		t.Errorf("%q is not a DNS label", long)
	}
}
