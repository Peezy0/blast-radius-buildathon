#!/bin/bash
# Trace the untraced `python -c` child of test_lazy_load and store its coverage in
# the collection output, attributed to that test (static context), so build_map
# picks it up via combine. Usage: subprocess_children.sh SQLGLOT_CHECKOUT COLLECT_OUT
set -eu
SRC=$1; OUT=$2
TID="tests/dialects/test_dialect.py::TestDialect::test_lazy_load"
# same code the test runs (tests/dialects/test_dialect.py test_lazy_load)
CODE="import sqlglot; assert len(sqlglot.Dialect._classes) == 1; print('Success')"
docker run --rm --platform linux/amd64 --network none -e COVERAGE_CORE=ctrace \
  -v "$SRC":/src/sqlglot:ro -v "$OUT":/out -e PYTHONPATH=/src/sqlglot -w /src/sqlglot br-collect \
  python -c "
import coverage, sys
c = coverage.Coverage(data_file='/out/.coverage', data_suffix='subprocess-lazy-load',
                      source=['/src/sqlglot/sqlglot'], context='collect|$TID|call')
c.start()
exec(compile(sys.argv[1], '<string>', 'exec'), {'__name__': '__main__'})
c.stop(); c.save()
" "$CODE"
