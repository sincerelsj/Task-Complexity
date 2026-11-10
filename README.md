# Task-Complexity

**How Task Complexity Shapes Information Search Patterns in Agents for Computer Use**

Seungju Lee · Minchul Cha · Hankuk University of Foreign Studies<br>
HAI ’26 · November 16–19, 2026 · Osaka, Japan

[Paper](https://doi.org/10.1145/3841580.3845692) · [Experiment](experiment/docs/experiment/README.md) · [Agents](experiment/agents/README.md) · [Analysis](experiment/analysis/README.md) · [Citation](experiment/paper/README.md#citation)

![Hand-drawn experiment overview](assets/experiment-overview-handdrawn.png)

## Overview

A web-based information-board experiment for studying how AI agents explore information before choosing an apartment. The task crosses **2/4/8/12 alternatives × 4/8/12 dimensions**, producing **12 conditions**.

The code provides click-to-reveal information cells, six agent integrations, shared behavioral logging, agent traces, and search-pattern analysis.

## Quick Start

From the repository root, run the following with Python 3.10+:

```bash
cd experiment
python -m pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000**. Enter a participant ID and select a condition. See [web setup](experiment/docs/experiment/README.md) for installation and [agent setup](experiment/agents/README.md) for Docker and API keys.

## Components

The [experiment folder](experiment/) contains the application, runners, and supporting files.

- [Experiment](experiment/docs/experiment/README.md): interface, conditions, and event logs.
- [Agents](experiment/agents/README.md): six implementations and execution commands.
- [Analysis](experiment/analysis/README.md): Search Index, ARC, and action classification.
- [Cost configuration](experiment/costs/README.md): enter your own model prices.
- [Documentation](experiment/docs/README.md): settings, batch runners, and development tools.
