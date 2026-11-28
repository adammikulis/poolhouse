package tail

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
)

type request struct {
	ID      int64  `json:"id"`
	Op      string `json:"op"`
	Service string `json:"service"`
	Target  string `json:"target"`
	Address string `json:"address"`
}

type reply struct {
	ID     int64   `json:"id"`
	OK     bool    `json:"ok"`
	Error  string  `json:"error,omitempty"`
	Local  string  `json:"local,omitempty"`
	Status *status `json:"status,omitempty"`
}

// Do answers one JSON request with one JSON reply. The command reads them from standard input and the Android app passes them
// in directly, so both speak the same protocol (docs/tailscale.md).
func (n *Node) Do(line []byte) []byte {
	var asked request
	answer := reply{}
	err := json.Unmarshal(line, &asked)
	if err != nil {
		err = errors.New("a request is one JSON object")
	} else {
		answer.ID = asked.ID
		err = n.answer(asked, &answer)
	}
	if err != nil {
		answer.Error = err.Error()
	}
	answer.OK = err == nil
	out, err := json.Marshal(answer)
	if err != nil {
		return []byte(`{"id":0,"ok":false,"error":"the reply could not be written"}`)
	}
	return out
}

func (n *Node) answer(asked request, into *reply) error {
	ctx, cancel := context.WithTimeout(context.Background(), connectWait)
	defer cancel()
	var err error
	switch asked.Op {
	case "status":
		into.Status, err = n.status(ctx)
	case "signin":
		into.Status, err = n.signIn(ctx)
	case "signout":
		err = n.signOut(ctx)
	case "expose":
		err = n.expose(asked.Service, asked.Target)
	case "unexpose":
		n.unexpose(asked.Service)
	case "forward":
		into.Local, err = n.forward(asked.Address)
	default:
		err = fmt.Errorf("%q is not a request the node knows", asked.Op)
	}
	return err
}
