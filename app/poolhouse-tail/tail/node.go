package tail

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"tailscale.com/envknob"
	"tailscale.com/logtail"
	"tailscale.com/tsnet"
)

const connectWait = 20 * time.Second

var errClosed = errors.New("the Tailscale node has stopped")

// Node is the running node: the services it carries in and the addresses it leads out to.
type Node struct {
	server *tsnet.Server

	mu       sync.Mutex
	exposed  map[string]net.Listener
	forwards map[string]net.Listener
	closed   bool
}

// Open starts the node that keeps its state in dir and asks to be called `poolhouse-<device>`. A node that is not signed in
// starts too; its status carries the sign-in link.
func Open(dir, device string) (*Node, error) {
	if !filepath.IsAbs(dir) {
		return nil, errors.New("the node keeps its state in a folder of its own, named by an absolute path")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	isolate()
	// The sign-in link adds this node to the account of whoever opens it, so tsnet's messages are dropped, not logged.
	silent := func(string, ...any) {}
	server := &tsnet.Server{Dir: dir, Hostname: hostName(device), Logf: silent, UserLogf: silent}
	if err := server.Start(); err != nil {
		return nil, fmt.Errorf("the Tailscale node did not start: %w", err)
	}
	return &Node{server: server, exposed: map[string]net.Listener{}, forwards: map[string]net.Listener{}}, nil
}

// isolate keeps the node apart from anything the environment says about another Tailscale setup (an auth key or a control
// address would join it somewhere else), asks no router for a port mapping and uploads no logs.
func isolate() {
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		upper := strings.ToUpper(name)
		if strings.HasPrefix(upper, "TS_") || strings.HasPrefix(upper, "TSNET_") {
			_ = os.Unsetenv(name)
		}
	}
	envknob.Setenv("TS_DISABLE_PORTMAPPER", "true")
	logtail.Disable()
}

// Close stops every listener and the node.
func (n *Node) Close() error {
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.closed {
		return nil
	}
	n.closed = true
	for _, held := range []map[string]net.Listener{n.exposed, n.forwards} {
		for key, listener := range held {
			_ = listener.Close()
			delete(held, key)
		}
	}
	return n.server.Close()
}

type status struct {
	State      string   `json:"state"`
	SignInURL  string   `json:"sign_in_url,omitempty"`
	Name       string   `json:"name"`
	DNSName    string   `json:"dns_name,omitempty"`
	Addresses  []string `json:"addresses,omitempty"`
	KeyExpires string   `json:"key_expires,omitempty"`
	Expired    bool     `json:"expired,omitempty"`
}

func (n *Node) status(ctx context.Context) (*status, error) {
	client, err := n.server.LocalClient()
	if err != nil {
		return nil, err
	}
	seen, err := client.StatusWithoutPeers(ctx)
	if err != nil {
		return nil, err
	}
	out := &status{State: seen.BackendState, SignInURL: seen.AuthURL, Name: n.server.Hostname}
	if me := seen.Self; me != nil {
		out.DNSName = strings.TrimSuffix(me.DNSName, ".")
		out.Expired = me.Expired
		if me.KeyExpiry != nil {
			out.KeyExpires = me.KeyExpiry.UTC().Format(time.RFC3339)
		}
	}
	for _, address := range seen.TailscaleIPs {
		out.Addresses = append(out.Addresses, address.String())
	}
	return out, nil
}

// signIn asks for a sign-in link: the first one, or a renewal while the node still runs.
func (n *Node) signIn(ctx context.Context) (*status, error) {
	client, err := n.server.LocalClient()
	if err != nil {
		return nil, err
	}
	if err := client.StartLoginInteractive(ctx); err != nil {
		return nil, err
	}
	tick := time.NewTicker(250 * time.Millisecond)
	defer tick.Stop()
	for {
		now, err := n.status(ctx)
		if err != nil {
			return nil, err
		}
		if now.SignInURL != "" {
			return now, nil
		}
		select {
		case <-ctx.Done():
			return nil, errors.New("Tailscale gave no sign-in link: check the network and try again")
		case <-tick.C:
		}
	}
}

func (n *Node) signOut(ctx context.Context) error {
	client, err := n.server.LocalClient()
	if err != nil {
		return err
	}
	return client.Logout(ctx)
}

// expose carries a service's tailnet port to target, a listener on this machine's loopback interface.
func (n *Node) expose(name, target string) error {
	carried, known := services[name]
	if !known {
		return fmt.Errorf("%q is not a service the node carries", name)
	}
	if err := checkTarget(target); err != nil {
		return err
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.closed {
		return errClosed
	}
	n.drop(name)
	listener, err := n.server.Listen("tcp", fmt.Sprintf(":%d", carried.port))
	if err != nil {
		return err
	}
	n.exposed[name] = listener
	go accept(listener, func(from net.Conn) { n.inbound(from, carried.who, target) })
	return nil
}

func (n *Node) unexpose(name string) {
	n.mu.Lock()
	defer n.mu.Unlock()
	n.drop(name)
}

// drop closes a service's listener; the caller holds the lock.
func (n *Node) drop(name string) {
	if old, held := n.exposed[name]; held {
		_ = old.Close()
		delete(n.exposed, name)
	}
}

func (n *Node) inbound(from net.Conn, who audience, target string) {
	defer from.Close()
	ctx, cancel := context.WithTimeout(context.Background(), connectWait)
	defer cancel()
	if who != anyCaller && !n.admitted(ctx, who, from.RemoteAddr().String()) {
		return
	}
	to, err := (&net.Dialer{}).DialContext(ctx, "tcp", target)
	if err != nil {
		return
	}
	splice(from, to)
}

// admitted asks Tailscale who the caller is. Anything it cannot answer is refused.
func (n *Node) admitted(ctx context.Context, who audience, remote string) bool {
	client, err := n.server.LocalClient()
	if err != nil {
		return false
	}
	mine, err := client.StatusWithoutPeers(ctx)
	if err != nil || mine.Self == nil {
		return false
	}
	theirs, err := client.WhoIs(ctx, remote)
	if err != nil || theirs.Node == nil {
		return false
	}
	self := identity{user: int64(mine.Self.UserID), tagged: mine.Self.Tags != nil && mine.Self.Tags.Len() > 0}
	caller := identity{user: int64(theirs.Node.User), tagged: len(theirs.Node.Tags) > 0}
	return admits(who, self, caller)
}

// forward opens (once per address) a loopback port that leads to address over the tailnet, and returns it.
func (n *Node) forward(address string) (string, error) {
	if err := checkDial(address); err != nil {
		return "", err
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.closed {
		return "", errClosed
	}
	if held, found := n.forwards[address]; found {
		return held.Addr().String(), nil
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return "", err
	}
	n.forwards[address] = listener
	go accept(listener, func(from net.Conn) { n.outbound(from, address) })
	return listener.Addr().String(), nil
}

func (n *Node) outbound(from net.Conn, address string) {
	defer from.Close()
	ctx, cancel := context.WithTimeout(context.Background(), connectWait)
	defer cancel()
	to, err := n.server.Dial(ctx, "tcp", address)
	if err != nil {
		return
	}
	// A name is looked up by Tailscale; whatever it resolved to, nothing is sent outside the tailnet.
	if !onTailnet(to.RemoteAddr()) {
		_ = to.Close()
		return
	}
	splice(from, to)
}

func onTailnet(remote net.Addr) bool {
	at, err := netip.ParseAddrPort(remote.String())
	if err != nil {
		return false
	}
	ip := at.Addr().Unmap()
	return tailnetV4.Contains(ip) || tailnetV6.Contains(ip)
}

func accept(listener net.Listener, each func(net.Conn)) {
	for {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		go each(conn)
	}
}

// splice copies both ways until each side has finished, passing on the end of one direction while the other still runs.
func splice(a, b net.Conn) {
	var both sync.WaitGroup
	one := func(to, from net.Conn) {
		defer both.Done()
		_, err := io.Copy(to, from)
		if half, can := to.(interface{ CloseWrite() error }); can && err == nil {
			_ = half.CloseWrite()
			return
		}
		_ = to.Close()
		_ = from.Close()
	}
	both.Add(2)
	go one(a, b)
	go one(b, a)
	both.Wait()
	_ = a.Close()
	_ = b.Close()
}
