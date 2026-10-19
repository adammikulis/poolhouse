//! Messages the node writes to a session on its own account: a claim whose time ran out, a subagent retired for idleness.
//! They arrive in the session's inbox as direct messages from `node`, a name no session can hold.

use serde_json::json;

use crate::error::Result;
use crate::node::Node;
use crate::row::Kind;

/// The name the node writes its own messages under.
pub const FROM: &str = "node";

/// Write ``body`` to the live session ``to`` of ``board``; nothing when it is gone.
pub fn tell(node: &mut Node, board: &str, to: &str, subject: &str, body: &str) -> Result<()> {
    if !node.can_receive(board, to)? {
        return Ok(());
    }
    let fields = json!({"from": FROM, "to": to, "type": "status", "subject": subject, "body": body});
    node.host(board)?.board.append(Kind::Message, FROM, fields, "")?;
    Ok(())
}
