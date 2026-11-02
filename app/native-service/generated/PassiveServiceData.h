// Generated passive data only. No installation, scope or authority.
#ifndef POOLHOUSE_PASSIVE_SERVICE_DATA_H
#define POOLHOUSE_PASSIVE_SERVICE_DATA_H
#include <stdint.h>
#include <stddef.h>
#define PH_PASSIVE_ABI_VERSION UINT32_C(1)
#define PH_PASSIVE_RESULT_OK INT32_C(0)
#define PH_PASSIVE_RESULT_INVALID_OUTPUT INT32_C(1)
#define PH_PASSIVE_AVAILABILITY_UNKNOWN UINT32_C(0)
#define PH_PASSIVE_AVAILABILITY_BINDING_UNAVAILABLE UINT32_C(1)
#define PH_PASSIVE_AVAILABILITY_PLIST_ABSENT UINT32_C(2)
#define PH_PASSIVE_AVAILABILITY_UNVERIFIED UINT32_C(3)
#define PH_PASSIVE_AVAILABILITY_NOT_REGISTERED UINT32_C(4)
#define PH_PASSIVE_AVAILABILITY_REGISTERED_ELIGIBLE UINT32_C(5)
#define PH_PASSIVE_AVAILABILITY_REQUIRES_CONSENT UINT32_C(6)
#define PH_PASSIVE_AVAILABILITY_NOT_FOUND UINT32_C(7)
#define PH_PASSIVE_REASON_UNKNOWN UINT32_C(0)
#define PH_PASSIVE_REASON_CONTAINING_APP_UNAVAILABLE UINT32_C(1)
#define PH_PASSIVE_REASON_CONTAINING_APP_INSPECTION_UNAVAILABLE UINT32_C(2)
#define PH_PASSIVE_REASON_PLIST_ABSENT UINT32_C(3)
#define PH_PASSIVE_REASON_LOCATION_UNVERIFIED UINT32_C(4)
#define PH_PASSIVE_REASON_PLIST_UNVERIFIED UINT32_C(5)
#define PH_PASSIVE_REASON_PLIST_READ_UNAVAILABLE UINT32_C(6)
#define PH_PASSIVE_REASON_PLIST_BINDING_MISMATCH UINT32_C(7)
#define PH_PASSIVE_REASON_SEAL_UNVERIFIED UINT32_C(8)
#define PH_PASSIVE_REASON_SIGNING_LABEL_MISMATCH UINT32_C(9)
#define PH_PASSIVE_REASON_PLIST_CHANGED UINT32_C(10)
#define PH_PASSIVE_REASON_ELIGIBILITY_ONLY UINT32_C(11)
#define PH_PASSIVE_REASON_STATUS_UNAVAILABLE UINT32_C(12)
#define PH_PASSIVE_REASON_CLOCK_UNAVAILABLE UINT32_C(13)
typedef struct {
    uint64_t observed_at;
    uint32_t availability;
    uint32_t reason;
} PhPassiveServiceObservationV1;
#ifdef __cplusplus
#define PH_PASSIVE_ASSERT static_assert
#define PH_PASSIVE_ALIGN alignof
extern "C" {
#else
#define PH_PASSIVE_ASSERT _Static_assert
#define PH_PASSIVE_ALIGN _Alignof
#endif
PH_PASSIVE_ASSERT(sizeof(PhPassiveServiceObservationV1) == 16, "passive size");
PH_PASSIVE_ASSERT(PH_PASSIVE_ALIGN(PhPassiveServiceObservationV1) == 8, "passive alignment");
PH_PASSIVE_ASSERT(offsetof(PhPassiveServiceObservationV1, observed_at) == 0, "passive offset");
PH_PASSIVE_ASSERT(offsetof(PhPassiveServiceObservationV1, availability) == 8, "passive offset");
PH_PASSIVE_ASSERT(offsetof(PhPassiveServiceObservationV1, reason) == 12, "passive offset");
// Trusted in-process caller supplies valid writable storage of this exact type.
// Null returns InvalidOutput without writing; Ok fully initializes every field.
int32_t ph_passive_service_observe_v1(PhPassiveServiceObservationV1 *output);
#ifdef __cplusplus
}
#endif
#undef PH_PASSIVE_ASSERT
#undef PH_PASSIVE_ALIGN
#endif
