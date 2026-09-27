#define DUCKDB_EXTENSION_MAIN
#include "csv_inhaler_extension.hpp"
#include "duckdb.hpp"
namespace duckdb {
static void LoadInternal(ExtensionLoader &loader) {
}
void CsvInhalerExtension::Load(ExtensionLoader &loader) {
	LoadInternal(loader);
}
std::string CsvInhalerExtension::Name() {
	return "csv_inhaler";
}
std::string CsvInhalerExtension::Version() const {
	return "";
}
} // namespace duckdb
extern "C" {
DUCKDB_CPP_EXTENSION_ENTRY(csv_inhaler, loader) {
	duckdb::LoadInternal(loader);
}
}
