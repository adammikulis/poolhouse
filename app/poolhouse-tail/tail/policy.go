// Package tail is Poolhouse's own Tailscale node: a userspace node (tsnet) that carries Poolhouse's three services and nothing
// else. It copies bytes and never ends TLS, so the pinned certificates and signed requests of each service decide every request.
// docs/tailscale.md gives the design.
package tail

import (
	"errors"
	"fmt"
	"net"
	"net/netip"
	"strconv"
	"strings"
)

// audience is who may reach a service. It only narrows: no service trusts a caller for being on the tailnet.
type audience int

const (
	// sameUser admits a device signed in as the Tailscale user this node is signed in as.
	sameUser audience = iota
	// anyCaller admits whatever the account's access rules let reach the node, people it is shared with included.
	anyCaller
)

type service struct {
	port uint16
	who  audience
}

// services is every port the node can have. There is no way to expose another one.
var services = map[string]service{
	"pool":  {port: 7447, who: sameUser},
	"phone": {port: 7450, who: sameUser},
	"guest": {port: 7453, who: anyCaller},
}

// identity is what Tailscale says a device is: the user it is signed in as, or tagged (no user).
type identity struct {
	user   int64
	tagged bool
}

func admits(who audience, self, caller identity) bool {
	if who == anyCaller {
		return true
	}
	return !self.tagged && !caller.tagged && self.user != 0 && caller.user == self.user
}

var (
	tailnetV4 = netip.MustParsePrefix("100.64.0.0/10")
	tailnetV6 = netip.MustParsePrefix("fd7a:115c:a1e0::/48")
)

// checkTarget holds a service's local listener to this machine's loopback interface.
func checkTarget(target string) error {
	at, err := netip.ParseAddrPort(target)
	if err != nil || !at.Addr().IsLoopback() || at.Port() == 0 {
		return fmt.Errorf("a service is forwarded to a loopback address and port, not %q", target)
	}
	return nil
}

// checkDial holds an outgoing connection to a tailnet address or `.ts.net` name, on one of the services' ports.
func checkDial(address string) error {
	host, portText, err := net.SplitHostPort(address)
	if err != nil {
		return fmt.Errorf("an address is a host and a port, not %q", address)
	}
	port, err := strconv.ParseUint(portText, 10, 16)
	if err != nil || !servicePort(uint16(port)) {
		return fmt.Errorf("port %q is not one of Poolhouse's tailnet ports", portText)
	}
	if ip, err := netip.ParseAddr(host); err == nil {
		if ip.Zone() == "" && (tailnetV4.Contains(ip) || tailnetV6.Contains(ip)) {
			return nil
		}
		return fmt.Errorf("%s is not a tailnet address", host)
	}
	if tailnetName(host) {
		return nil
	}
	return errors.New("only a tailnet address or a .ts.net name is dialed")
}

func servicePort(port uint16) bool {
	for _, s := range services {
		if s.port == port {
			return true
		}
	}
	return false
}

func tailnetName(host string) bool {
	name := strings.ToLower(strings.TrimSuffix(host, "."))
	if len(name) > 253 || !strings.HasSuffix(name, ".ts.net") {
		return false
	}
	for _, label := range strings.Split(name, ".") {
		if !dnsLabel(label) {
			return false
		}
	}
	return true
}

func dnsLabel(label string) bool {
	if label == "" || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
		return false
	}
	for _, r := range label {
		if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '-') {
			return false
		}
	}
	return true
}

// hostName is the machine name the node asks for: `poolhouse-` and what of the device's name a DNS label can hold.
func hostName(device string) string {
	var kept strings.Builder
	dash := false
	for _, r := range strings.ToLower(device) {
		switch {
		case r >= 'a' && r <= 'z' || r >= '0' && r <= '9':
			if dash && kept.Len() > 0 {
				kept.WriteByte('-')
			}
			dash = false
			kept.WriteRune(r)
		default:
			dash = true
		}
	}
	name := "poolhouse"
	if kept.Len() > 0 {
		name += "-" + kept.String()
	}
	if len(name) > 63 {
		name = strings.TrimRight(name[:63], "-")
	}
	return name
}
