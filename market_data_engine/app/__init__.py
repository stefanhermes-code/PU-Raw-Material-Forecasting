"""The market-data engine's package.

This file exists for one reason, and it is not style. Without it `app` is a NAMESPACE package, and
Python's import machinery always prefers a regular module to a namespace package - so the
repository root's `app.py` (the Streamlit application) won `import app` no matter where
`market_data_engine` sat on `sys.path`. Any test that put the root on the path, for
`market_selection_ui`, broke `from app.db import ...` in every test collected after it, and which
tests those were depended on collection order.

The masterplan recorded that collision as a known defect with "no path fix exists". That was true:
no amount of path ordering can beat the namespace rule. This is the fix - `app` is a regular
package now, and the path order in `pyproject.toml` decides.
"""
