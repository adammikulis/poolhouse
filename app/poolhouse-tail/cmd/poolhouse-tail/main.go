// Command poolhouse-tail runs Poolhouse's Tailscale node for the daemon that starts it: one JSON request per line on standard
// input, one reply per line on standard output. It ends when its input closes, so it never outlives that daemon.
package main

import (
	"bufio"
	"flag"
	"fmt"
	"io"
	"os"
	"sync"

	"poolhouse.local/tail/tail"
)

const longestRequest = 1 << 16

func main() {
	dir := flag.String("dir", "", "the folder the node keeps its state in (an absolute path)")
	name := flag.String("name", "", "the name of this device")
	flag.Parse()
	node, err := tail.Open(*dir, *name)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	err = serve(node.Do, os.Stdin, os.Stdout)
	_ = node.Close()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// serve answers each line as it arrives, so a request that waits (a sign-in link) holds up no other.
func serve(do func([]byte) []byte, in io.Reader, out io.Writer) error {
	var writing sync.Mutex
	lines := bufio.NewScanner(in)
	lines.Buffer(make([]byte, 4096), longestRequest)
	for lines.Scan() {
		line := append([]byte(nil), lines.Bytes()...)
		go func() {
			answer := append(do(line), '\n')
			writing.Lock()
			defer writing.Unlock()
			// A reply nobody can read means the daemon is gone, which closes the input too.
			_, _ = out.Write(answer)
		}()
	}
	return lines.Err()
}
