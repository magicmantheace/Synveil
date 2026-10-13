// SPDX-License-Identifier: MPL-2.0
#include "pressure_diag.h"
#include <assert.h>
#include <stddef.h>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    unsigned percent = 9999;
    const char *valid = "some avg10=1.25 avg60=0.50 avg300=0.02 total=12345\n";
    assert(synveil_pressure_parse_some(valid, &percent));
    assert(percent == 13u);
    assert(synveil_pressure_parse_some(
        "some avg10=100.00 avg60=0.00 avg300=0.00 total=0\n", &percent));
    assert(percent == 1000u);
    const char *invalid[] = {
        "", "full avg10=1.00 avg60=0.00 avg300=0.00 total=1\n",
        "some avg10=-1.00 avg60=0.00 avg300=0.00 total=1\n",
        "some avg10=NaN avg60=0.00 avg300=0.00 total=1\n",
        "some avg10=100.01 avg60=0.00 avg300=0.00 total=1\n",
        "some avg10=0.10 avg60=101.00 avg300=0.00 total=1\n",
        "some avg10=0.10 avg60=0.00 avg300=0.00 total=-1\n",
        "some avg10=0.10 avg60=0.00 avg300=0.00 total=18446744073709551616\n",
        "some avg10=0.10 avg60=0.00 avg300=0.00 total=1 extra=1\n",
        "some avg10=0.10 avg60=0.00 avg300=0.00 total=1",
        "some avg10=0.10 avg60=0.00 avg300=0.00 total=1\nextra",
        "some avg10=0.1 avg60=0.00 avg300=0.00 total=1\n",
        "some avg10=0.10 avg60=0.00 avg300=0.00 total=1.0\n",
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
        assert(!synveil_pressure_parse_some(invalid[i], &percent));
    assert(synveil_pressure_some_avg10(argv[1], &percent));
    assert(percent == 13u);
    assert(!synveil_pressure_some_avg10(argv[2], &percent));
    assert(!synveil_pressure_some_avg10(NULL, &percent));
    return 0;
}
