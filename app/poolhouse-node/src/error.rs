//! The ways a board operation is refused.

use std::fmt;

/// Why something was not done. `Damaged` is the only kind that can mark an origin damaged.
#[derive(Debug)]
pub enum Error {
    /// A log copy that is forged, forked or broken.
    Damaged(String),
    /// Rows that do not start where the held copy ends.
    Gap(String),
    /// Something past what a device keeps.
    Quota(String),
    /// A request or row that is malformed or not allowed.
    Invalid(String),
    /// The caller is not who it needs to be.
    Denied(String),
    /// A session holds no grant for what it asked to do; `grant` names the action, the resource, the session and the
    /// device, so a person's client can sign one for exactly that.
    NoGrant { message: String, grant: serde_json::Value },
    /// The disk said no.
    Io(std::io::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    /// A short stable code for the wire.
    pub fn code(&self) -> &'static str {
        match self {
            Error::Damaged(_) => "damaged",
            Error::Gap(_) => "gap",
            Error::Quota(_) => "quota",
            Error::Invalid(_) => "invalid",
            Error::Denied(_) | Error::NoGrant { .. } => "denied",
            Error::Io(_) => "io",
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Damaged(m) | Error::Gap(m) | Error::Quota(m) | Error::Invalid(m) | Error::Denied(m) | Error::NoGrant { message: m, .. } => {
                write!(f, "{}: {m}", self.code())
            }
            Error::Io(e) => write!(f, "io: {e}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e)
    }
}

impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Error::Invalid(e.to_string())
    }
}
