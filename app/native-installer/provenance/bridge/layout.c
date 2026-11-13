#include "layout.h"
#include "libprovenance.h"
#include <stddef.h>

_Static_assert(sizeof(void *) == 8, "unsupported pointer width");
_Static_assert(sizeof(uint64_t) == 8 && sizeof(uint32_t) == 4 && sizeof(int) == 4, "unsupported ABI widths");
#define PH_BYTES_OFFSET(name) {offsetof(ph_bytes,name),sizeof(((ph_bytes *)0)->name)},
#define PH_VERSION_OFFSET(name) {offsetof(ph_version,name),sizeof(((ph_version *)0)->name)},
#define PH_INPUT_OFFSET(name) {offsetof(ph_provenance_input,name),sizeof(((ph_provenance_input *)0)->name)},
#define PH_RESULT_OFFSET(name) {offsetof(ph_provenance_result,name),sizeof(((ph_provenance_result *)0)->name)},
typedef struct { uint64_t offset, size; } ph_field_layout;
typedef struct {
 uint32_t version;
 uint64_t bytes_size, bytes_align, version_size, version_align, input_size, input_align, result_size, result_align;
 uint32_t bytes_count, version_count, input_count, result_count;
 ph_field_layout bytes[PH_BYTES_COUNT], version_fields[PH_VERSION_COUNT], input[PH_INPUT_COUNT], result[PH_RESULT_COUNT];
} ph_provenance_layout;
static const ph_provenance_layout layout = {
 .version=1,
 .bytes_size=sizeof(ph_bytes), .bytes_align=_Alignof(ph_bytes),
 .version_size=sizeof(ph_version), .version_align=_Alignof(ph_version),
 .input_size=sizeof(ph_provenance_input), .input_align=_Alignof(ph_provenance_input),
 .result_size=sizeof(ph_provenance_result), .result_align=_Alignof(ph_provenance_result),
 .bytes_count=PH_BYTES_COUNT, .version_count=PH_VERSION_COUNT, .input_count=PH_INPUT_COUNT, .result_count=PH_RESULT_COUNT,
 .bytes={PH_BYTES_FIELDS(PH_BYTES_OFFSET)}, .version_fields={PH_VERSION_FIELDS(PH_VERSION_OFFSET)},
 .input={PH_INPUT_FIELDS(PH_INPUT_OFFSET)}, .result={PH_RESULT_FIELDS(PH_RESULT_OFFSET)},
};
uint64_t ph_provenance_layout_query(uint32_t version, uint32_t type, uint32_t property, uint32_t index) {
 if (version != 1) return UINT64_MAX;
 const ph_field_layout *fields=NULL; uint64_t size=0, align=0, count=0;
 switch (type) {
  case PH_LAYOUT_BYTES: fields=layout.bytes; size=layout.bytes_size; align=layout.bytes_align; count=layout.bytes_count; break;
  case PH_LAYOUT_VERSION: fields=layout.version_fields; size=layout.version_size; align=layout.version_align; count=layout.version_count; break;
  case PH_LAYOUT_INPUT: fields=layout.input; size=layout.input_size; align=layout.input_align; count=layout.input_count; break;
  case PH_LAYOUT_RESULT: fields=layout.result; size=layout.result_size; align=layout.result_align; count=layout.result_count; break;
  default: return UINT64_MAX;
 }
 switch (property) {
  case PH_LAYOUT_SIZE: return index == 0 ? size : UINT64_MAX;
  case PH_LAYOUT_ALIGN: return index == 0 ? align : UINT64_MAX;
  case PH_LAYOUT_COUNT: return index == 0 ? count : UINT64_MAX;
  case PH_LAYOUT_OFFSET: return index < count ? fields[index].offset : UINT64_MAX;
  case PH_LAYOUT_WIDTH: return index < count ? fields[index].size : UINT64_MAX;
  default: return UINT64_MAX;
 }
}
