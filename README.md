# Bondflow — EUR Balance Sheet Valuation

[Open the public calculator](https://alarabuyukates-pixel.github.io/ValuationEngine/)

One workflow for fixed, floating and zero-coupon bonds and fixed or floating mortgages. All positions use EUR. Other explicit currencies are rejected. Calculations run locally in the browser using Python/Pyodide; portfolio workbooks are not uploaded. Positions last for the browser session: download current inputs before reloading.

## Workflow

1. **Data & valuation:** import a mixed Excel workbook or enter a position using one conditional form. Download the mixed template for examples. Choose replace or append, review mappings, then load the balance sheet. Loading does not automatically value.
2. **Balance sheet:** search all products, inspect terms, edit or remove a selected position, and download the current inputs. Payment frequency, reset interval, index tenor, current outstanding and current instalment are separate fields.
3. **Results & validation:** run all positions together. Failed rows are isolated. Review valuations, cash flows, rate resets and errors. Download all results or select one position for a validation workbook.
4. **Market curves:** inspect the dated ECB curve or import provider zero rates / discount factors. This workspace has no licensed Bloomberg/LSEG API connection.

Duplicate product IDs remain separate positions. Product reports are selected by position row, with original workbook file, worksheet and row retained in valuation outputs. Changing positions or market curves invalidates displayed results.

## Floating rates and independent resets

Both floating bonds and floating mortgages use the same `rate_projection.py` schedule. Current-period reference rates come from the supplied known fixing. Future resets use:

`forward = (projection_DF(reset) / projection_DF(index_end) - 1) / index_year_fraction`

`contractual_rate = reference_rate + fixed_spread`

The full index tenor is used even for the last reset; the projection curve may therefore need coverage beyond contractual maturity. Payment frequency and reset frequency are independent: monthly payments with quarterly resets are supported. This version requires resets on payment boundaries and reset intervals that are integer multiples of payment intervals. Set `Next reset date` to anchor an explicit phase; otherwise the schedule starts from origination. Resets inside a payment period, business-day adjustment, fixing lag and overnight compounding are not implemented.

Choose `Index mode = named` for EURIBOR or another term index. Named projection curves must have the matching index tag. EURIBOR uses ACT/360 and its named tenor. Choose `Index mode = curve` with an explicit tenor to use any suitable loaded curve as an explicitly labelled curve-implied reference. ECB sovereign rates are never relabelled EURIBOR. Flat discount/projection rates are explicit assumptions, not market quotes. Spread and fixing inputs are percentage points: 0.5 means 50 bps. Negative floating rates are allowed without an implicit floor.

## Mortgage calculations

Repayment: annuity, linear or interest-only. Mortgage schedules use regular periods and maturities aligned to origination; terms are at most 50 years. Periodic interest is opening balance × annual nominal coupon / payments per year. Accrued interest prorates the current interest amount by elapsed calendar days. Payments on settlement are excluded.

Fixed loans reconstruct balances from original principal assuming past scheduled payments were paid. Seasoned floating loans require **current outstanding principal**. Seasoned floating annuities also require **current contractual instalment**; future resets recalculate that instalment using remaining balance and term. Floating linear loans amortize current outstanding evenly over remaining payments. Negative amortization is rejected. Prices per 100 use current outstanding.

Mortgage discount spread is in **basis points**, separate from contractual floating spread. Effective discount factor is `DF(payment)/DF(settlement) × exp(-discount_spread_bps/10000 × ACT/365F)`. No credit loss, arrears, prepayment, fees, caps/floors or fixed-then-floating hybrid is modeled. Positions represent positive lender assets; liability signs, net balance-sheet aggregation and regulatory interest-rate-risk measures remain future work.

## Product validation Excel

Select a position under Results & validation. Successful reports include Summary, Inputs, Curve inputs, Reset audit, Cash flow audit, Checks and Run info. Formulas reconstruct full-tenor forwards, coupon plus spread, interest, total payments, effective discount factors, PV and principal/balance reconciliation. Formula cells include cached values and recalculate in Excel. Failed products export their inputs and errors without fabricated valuation checks.

PASS means internal arithmetic reconciliation within EUR 0.01, **not independent market-price validation**. The workbook captures one run; editing terms does not regenerate the amortization schedule or re-bootstrap/interpolate a curve. Rerun the application after changing contractual terms or market data.

## Market data

`ECB_EUR_AAA` is the ECB euro-area AAA government zero curve, a sovereign benchmark, not an EURIBOR projection or €STR OIS curve. Observation date, source link, retrieval date and snapshot age are visible. Refresh from ECB retains the previous snapshot if the request fails. The bundled snapshot is refreshed during deployment where available.

Use Market curves → Download curve template for provider imports, or include a `Curves` sheet in the mixed portfolio. Required metadata: Curve ID, Currency, As of, Pillar date; enter exactly one Zero rate % or Discount factor per pillar. Repeat consistent Source, Reference index, Compounding and Day count metadata. Named projection curves need their reference-index tag. JSON uses decimal zero rates; Excel uses percentage points or native percentage cells. Curves interpolate log discount factors without extrapolation. Par swap yields must be bootstrapped externally first. Provider data use depends on the user's data entitlement.

## Local development and verification

```sh
python scripts/prepare_vendor.py
python -m http.server 8000 --directory dist
```

Open http://localhost:8000. Windows users can substitute `py` for `python`. Browser dependencies are pinned and checksum-verified. Use HTTP rather than opening the HTML file directly.

```sh
PYTHONPATH=dist python -m unittest discover -s tests -v
node tests/runtime.mjs
npm ci --ignore-scripts
npm run test:ui
```

Tests cover valuation mathematics, mixed-row isolation, dated curves, floating reset schedules, mortgage balances and the real Python WebAssembly runtime. Interface integration uses JSDOM with actual SheetJS and Pyodide, including mixed import, edits, result invalidation, Excel formulas and workbook round trips.

GitHub Actions validates and deploys `dist/` on changes to main. `scripts/package_source.py` generates the downloadable source ZIP. The earlier ChatGPT-hosted site is a separate deployment and is not updated by this repository.
