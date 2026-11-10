# Stimuli and Conditions

These three CSV files are experimental inputs, not participant records:

- `apartments.csv`: apartment identifiers and attribute values.
- `dimensions.csv`: dimension identifiers, display labels, and visibility levels.
- `conditions.csv`: the 12 alternative-count × dimension-count combinations.

The app selects the first N apartments and the dimensions whose visibility level is at most the requested dimension count. Preserve file order and values for reproduction. UTF-8 with or without a BOM is supported.

Behavioral records are written to `../logs/` and are never included here. See [experimental controls](../docs/10_experimental_control_matrix.md) before changing inputs.
