# Contest Rule Fields

This project stores contest scoring rules as key/value pairs on each annual edition.
The scoring engine reads the values as plain text and converts them to the expected type.

## Field Reference

| Key | Expected value | Notes |
| --- | --- | --- |
| `base_qso_points` | integer | Default points for each valid QSO. |
| `qso_points_cw` | integer | Overrides base points for CW QSOs when greater than zero. |
| `qso_points_ssb` | integer | Overrides base points for SSB/PHONE QSOs when greater than zero. |
| `qso_points_digi` | integer | Overrides base points for digital QSOs when greater than zero. |
| `digi_modes` | comma-separated mode list | Mode tokens treated as digital, for example `DIGI,RTTY,FT8,PSK31`. |
| `allowed_bands` | comma-separated band list | If set, QSOs outside the list are marked invalid. Example: `80M,40M,20M`. |
| `allowed_modes` | comma-separated mode list | If set, QSOs outside the list are marked invalid. Example: `CW,SSB`. |
| `dupe_scope` | one of `band_mode`, `band`, `global` | Controls what counts as a duplicate QSO. |
| `bonus_per_unique_prefix` | integer | Additive bonus for each unique worked prefix. |
| `multiplier_per_unique_band` | integer | Multiplier increment for each unique worked band. |
| `multiplier_per_unique_mode` | integer | Multiplier increment for each unique worked mode. |
| `multiplier_per_unique_prefix` | integer | Multiplier increment for each unique worked prefix. |
| `premium_station_points` | comma-separated `CALL:POINTS` list | Adds extra points for specific worked stations, for example `SP9ABC:5,K1TTT:10`. |
| `require_cross_log_match` | boolean | Accepts `true/false`, `yes/no`, `on/off`, or `1/0`. Requires a reciprocal QSO in another log. |
| `cross_log_time_tolerance_min` | integer | Allowed minute difference for reciprocal QSO matching. |
| `cross_log_check_exchange` | boolean | When true, the reciprocal sent and received exchanges must also match. |

## Validation Notes

- The upload form validates submission metadata before parsing Cabrillo data.
- Contest rules are enforced during scoring, not during the initial file upload.
- Rule-based validation currently covers band, mode, duplicate scope, and cross-log reciprocity.
- Bonus and multiplier fields affect scoring only; they do not reject a submission.
- Participant categories are informational in the current schema and are not used as hard validation rules.

## Practical Values

Common patterns that work well:

- `allowed_modes`: `CW,SSB`
- `allowed_bands`: `80M,40M,20M`
- `dupe_scope`: `band_mode` for the strictest common case, `band` if mode should not matter, or `global` if the call is only allowed once.
- `require_cross_log_match`: `false` for standard single-log scoring, `true` for events that require a matching log from the other station.
- `premium_station_points`: `SP9ABC:3,SP0XYZ:5`
