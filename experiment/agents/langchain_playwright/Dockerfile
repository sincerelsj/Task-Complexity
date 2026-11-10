# LangChain PlaywrightBrowserToolkit runner.
#
# DOM-level browsing agent driven by LangChain's PlaywrightBrowserToolkit.
# The toolkit's tools dispatch via Playwright/CDP — the model never sees
# screenshots, only extracted text and CSS selectors.
#
# Build (from agents/):
#   docker build -f langchain_playwright/Dockerfile -t task-complexity/lcpw:latest .

FROM task-complexity/agents-base:latest

USER root

# LangChain core + community (toolkit) + playwright + provider bindings.
# beautifulsoup4 / lxml are listed because some toolkit tools parse pages
# with them and the package is otherwise an optional dep that LangChain
# doesn't pull transitively in newer versions.
# 2026-05-27: tightened to bounded ranges. langchain-community is the most
# volatile of these — toolkit signatures (PlayWrightBrowserToolkit) have
# changed across minor releases; <0.4 hard cap. Lock with `pip freeze`
# post-build for full reproducibility.
RUN pip3 install --no-cache-dir --break-system-packages \
    'langchain>=0.3.0,<0.4.0' \
    'langchain-community>=0.3.0,<0.4.0' \
    'langchain-anthropic>=0.2.0,<1.0.0' \
    'langchain-openai>=0.2.0,<1.0.0' \
    'langchain-google-genai>=2.0.0,<3.0.0' \
    'playwright>=1.45.0,<2.0.0' \
    'beautifulsoup4>=4.12.0,<5.0.0' \
    'lxml>=5.0.0,<7.0.0'

# We rely on Playwright finding the system Chromium (added by the base image)
# via its default executable path search. If that fails on a particular
# platform, set PLAYWRIGHT_BROWSERS_PATH=0 + run `playwright install chromium`
# at the cost of ~150MB extra image size.

COPY langchain_playwright/runner/ /home/computeruse/runner/
RUN chown -R computeruse:computeruse /home/computeruse/runner

USER computeruse

EXPOSE 6080

ENTRYPOINT ["/home/computeruse/image/entrypoint.sh"]
