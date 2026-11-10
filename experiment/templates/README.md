# Experiment Templates

`base.html` defines the shared page shell. `index.html` selects a participant and condition; `experiment.html` renders masked cells and choice buttons; `complete.html` confirms completion.

Cell values must stay out of the initial HTML and element attributes. The browser fetches them only after a click. A choice button submits immediately. Preserve this behavior and the 1920×1200 display layout when editing presentation.

See the [web experiment guide](../docs/experiment/README.md) for setup, routes, and logs.
