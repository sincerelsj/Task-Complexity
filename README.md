# How Task Complexity Shapes Information Search Patterns in Agents for Computer Use

**Seungju Lee · Minchul Cha** · Hankuk University of Foreign Studies<br>
**HAI ’26** · November 16–19, 2026 · Osaka, Japan

[Paper](https://doi.org/10.1145/3841580.3845692) · [Experiment](docs/experiment/README.md) · [Agents](agents/README.md) · [Analysis](analysis/README.md) · [Citation](paper/README.md#citation)

![Hand-drawn overview: vary task complexity, explore and choose, then trace information-search behavior](assets/experiment-overview-handdrawn.png)

## Overview

How does task complexity shape what an AI agent explores before making a decision? This project reimplements Payne’s information-board task as a web-based apartment-selection experiment: **4 alternative counts × 3 dimension counts = 12 conditions**.

The paper examines search amount and search structure, using Payne’s published human results as a historical comparison. The implementation provides a shared browser task, behavioral logs, and six agent integrations. **The six supported frameworks are a software capability; the paper reports a narrower ACU study.**

### Experiment Interface

![Original experiment interface with revealed and masked apartment attributes](assets/experiment-interface-original.png)

*Original experiment screenshot, reproduced without editing.*

## Quick Start

With Python 3.10+ and the dependencies in `requirements.txt` installed:

```bash
python app.py
```

Open **http://localhost:5000**, enter a new participant ID, and choose a condition. The web task needs no API key. See [environment setup](docs/experiment/README.md) for installation and [agent setup](agents/README.md) for Docker and API configuration.

## Explore the Code

- [Web experiment](docs/experiment/README.md): interface, routes, conditions, and behavioral logging.
- [Agent implementations](agents/README.md): six runners, shared prompts, and trace format.
- [Analysis](analysis/README.md): search proportion, Search Index, search variability, and ARC.
- [Cost configuration](costs/README.md): user-entered prices, cache accounting, and estimate limitations.
- [Reproducibility](docs/README.md): controls, verification, and release scope.

Detailed implementation notes live with each component. Experiment logs and credentials are excluded. Paper metadata and BibTeX are in [paper/](paper/README.md); the source-code license remains to be selected.
