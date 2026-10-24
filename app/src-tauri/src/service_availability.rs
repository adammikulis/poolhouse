//! Optional in-process diagnostics only. No service scope, public command or startup gate.
#[path = "generated/passive_service.rs"]
mod data;
use data::{PassiveServiceData, PassiveServiceObservationV1};
use std::sync::{Arc, OnceLock};

unsafe extern "C" {
    fn ph_passive_service_observe_v1(output: *mut PassiveServiceObservationV1) -> i32;
}

fn observe() -> Option<PassiveServiceData> {
    // Raw contains only integers. Zero initialization is valid and fails checked decoding
    // if a broken bridge reports success without completely filling the output.
    let mut raw: PassiveServiceObservationV1 = unsafe { std::mem::zeroed() };
    // The linked generated v1 ABI receives valid, uniquely owned writable storage.
    let result = unsafe { ph_passive_service_observe_v1(&mut raw) };
    PassiveServiceData::decode(result, raw)
}

pub(crate) struct OptionalDiagnostic(Arc<OnceLock<Option<PassiveServiceData>>>);
impl OptionalDiagnostic {
    pub(crate) fn start() -> Self {
        let state = Arc::new(OnceLock::new());
        let worker = Arc::clone(&state);
        // No join, dispatch to the UI thread, retry or error propagation into launch.
        if std::thread::Builder::new().name("passive-service-diagnostic".into()).spawn(move || {
            let _ = worker.set(observe());
        }).is_err() {
            let _ = state.set(None);
        }
        Self(state)
    }

    pub(crate) fn snapshot(&self) -> Option<&PassiveServiceData> {
        self.0.get().and_then(Option::as_ref)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use data::{PassiveServiceAvailabilityTag, PassiveServiceReasonCode, PassiveServiceResult};

    #[test]
    fn actual_swift_ffi_rejects_null_and_observes_outside_app() {
        // This binary is an inert CLI, never a fabricated containing app.
        assert_eq!(PassiveServiceResult::from_raw(unsafe { ph_passive_service_observe_v1(std::ptr::null_mut()) }),
                   Some(PassiveServiceResult::InvalidOutput));
        let observation = observe().expect("real bridge must fill valid data");
        assert_eq!(observation.availability, PassiveServiceAvailabilityTag::BindingUnavailable);
        assert_eq!(observation.reason, PassiveServiceReasonCode::ContainingAppUnavailable);
        assert!(observation.observed_at > 0);
    }
}
