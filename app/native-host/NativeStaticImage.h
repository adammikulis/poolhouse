#ifndef PH_NATIVE_STATIC_IMAGE_H
#define PH_NATIVE_STATIC_IMAGE_H
#include "NativeIdentity.h"

/* This predicate validates one Security.framework code object against a
 * separately authenticated requirement and exact OS observation. It does not
 * hold paths, verify publisher provenance, issue a witness or authorize use. */
bool ph_native_static_image_matches(SecStaticCodeRef code, SecRequirementRef requirement,
                                    const ph_native_identity_observation *expected);
#endif
