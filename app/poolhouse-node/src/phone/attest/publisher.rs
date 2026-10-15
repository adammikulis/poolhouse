use super::{AttestationError, Result};

pub(super) struct Policy {
    pub signer: [u8; 32],
    pub minimum_version: u32,
}

const APPROVED: Option<Policy> = None;

impl Policy {
    fn validate(&self) -> Result<&Self> {
        if self.signer == [0; 32]
            || self.minimum_version == 0
            || self.minimum_version > i32::MAX as u32
        {
            return Err(AttestationError::Application);
        }
        Ok(self)
    }
}

pub(super) fn approved() -> Result<&'static Policy> {
    APPROVED
        .as_ref()
        .ok_or(AttestationError::Application)?
        .validate()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn publisher_policy_requires_reviewed_source_and_valid_metadata() {
        assert!(matches!(approved(), Err(AttestationError::Application)));
        for policy in [
            Policy {
                signer: [0; 32],
                minimum_version: 1,
            },
            Policy {
                signer: [7; 32],
                minimum_version: 0,
            },
            Policy {
                signer: [7; 32],
                minimum_version: u32::MAX,
            },
        ] {
            assert!(policy.validate().is_err());
        }
        assert!(Policy {
            signer: [7; 32],
            minimum_version: 1
        }
        .validate()
        .is_ok());
    }
}
