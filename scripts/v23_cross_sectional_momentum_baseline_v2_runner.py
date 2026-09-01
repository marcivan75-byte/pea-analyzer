from pathlib import Path

# Mechanical bug fix only: pandas DataFrame.isin is a method, so column access must use ['isin'].
# The frozen strategy logic and all hyperparameters remain unchanged.
src_path = Path(__file__).with_name('v23_cross_sectional_momentum_baseline_v2.py')
src = src_path.read_text(encoding='utf-8')
old = "g.sort_values('rank').isin.tolist()"
new = "g.sort_values('rank')['isin'].tolist()"
if src.count(old) != 1:
    raise SystemExit('BLOCK_V23_HOTFIX_PATTERN')
src = src.replace(old, new)
exec(compile(src, str(src_path), 'exec'), {'__name__': '__main__', '__file__': str(src_path)})
