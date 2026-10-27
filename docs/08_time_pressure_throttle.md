# Time-pressure throttle policy

> 작성일: 2026-05-27
> 목적: time-pressure 조건에서 rate-limit 방어용 sleep이 모델의 180초 판단 시간에 섞이지 않도록 운영 규칙을 고정한다.

## 결론

Time-pressure 실행도 API rate-limit 방어를 위해 턴 간 sleep을 사용한다. 단, 이 sleep은 모델에게 주어진 판단 시간에서 제외한다.

정책은 다음과 같다.

1. 첫 API/LLM 호출은 즉시 보낸다.
2. 두 번째 호출부터 `TurnSleepSeconds` 만큼 sleep한다.
3. time-pressure deadline은 `decision_seconds = wall_seconds - throttle_sleep_seconds` 기준으로 판정한다.
4. `wall_seconds`는 실제 세션 전체 소요 시간이고, `decision_seconds`가 모델에게 준 실질 판단 시간이다.

즉, 180초 time-pressure 조건에서 `TurnSleepSeconds = 40`이고 LLM 호출이 4번 일어났다면:

```text
throttle_sleep_seconds = 3 * 40 = 120
decision_seconds       = wall_seconds - 120
deadline 판정          = decision_seconds >= 180
```

## 적용 파일

### Time-pressure PowerShell runners

아래 6개 스크립트는 모두 `$TurnSleepSeconds = 40`을 설정하고 `--turn-sleep-seconds $TurnSleepSeconds`를 컨테이너에 전달한다.

- `run_anthcu_time_pressure.ps1`
- `run_oaicu_time_pressure.ps1`
- `run_gemcu_time_pressure.ps1`
- `run_cldcr_time_pressure.ps1`
- `run_brouse_time_pressure.ps1`
- `run_lcpw_time_pressure.ps1`

콘솔에는 다음 의미의 안내가 출력된다.

```text
throttle : 40s after first API call (excluded from decision time)
```

### Direct-loop frameworks

아래 4개는 외부 loop가 직접 API 턴을 관리한다. 이미 `turn > 1`일 때만 sleep한다.

- `agents/computer_use/runner/loop.py` (ANTHCU)
- `agents/openai_computer_use/runner/loop.py` (OAICU)
- `agents/gemini_computer_use/runner/loop.py` (GEMCU)
- `agents/claude_in_chrome/runner/loop.py` (CLDCR)

핵심 invariant:

```python
if turn > 1 and turn_sleep_seconds > 0:
    time.sleep(turn_sleep_seconds)
    result.throttle_sleep_seconds += turn_sleep_seconds

decision_elapsed = elapsed - result.throttle_sleep_seconds
```

### Library-internal-loop frameworks

BROUSE와 LCPW는 라이브러리가 내부에서 LLM loop를 돌리므로 LangChain callback으로 throttle을 건다.

- `agents/browser_use/runner/loop.py`
- `agents/langchain_playwright/runner/loop.py`

2026-05-27 수정으로 callback은 첫 LLM 호출에서는 sleep하지 않고, 두 번째 호출부터 sleep한다.

또한 callback 내부에서 `decision_elapsed = wall - throttle_sleep_seconds`를 계산해 time-pressure deadline을 넘긴 다음 LLM 호출을 막는다.

제한: 라이브러리 내부 루프는 외부 코드가 매 턴 system prompt를 다시 포맷할 수 없다. 따라서 BROUSE/LCPW의 time-pressure prompt는 시작 시점의 `TIME REMAINING`을 보여주고, 이후 deadline enforcement는 callback/AgentExecutor 쪽에서 수행한다. ANTHCU/OAICU/GEMCU/CLDCR은 매 API 호출마다 remaining seconds를 새로 계산해 보낸다.

## Trace fields

`agents/_shared/trace_logger.py`는 summary.json에 다음 필드를 기록한다.

- `wall_seconds`: 실제 벽시계 시간
- `throttle_sleep_seconds`: rate-limit 방어용 sleep 누적 시간
- `decision_seconds`: `wall_seconds - throttle_sleep_seconds`

time-pressure 분석에서는 `decision_seconds`를 180초 deadline 판정과 비교해야 한다.

## 검증 체크리스트

첫 재빌드 후 각 프레임워크 time-pressure C1 또는 C6 1회에서 다음을 확인한다.

- 콘솔 JSON에 `throttle_sleep_seconds`가 0보다 크다.
- Layer-2 `summary.json`에 `decision_seconds`가 있다.
- 직접-loop 4개는 `throttle_sleep_seconds == max(iterations - 1, 0) * 40`에 가깝다.
- BROUSE/LCPW는 라이브러리 내부 LLM 호출 수 기준이라 `iterations`와 정확히 일치하지 않을 수 있다. 대신 첫 호출 전 sleep이 없어야 하고, `decision_seconds = wall_seconds - throttle_sleep_seconds`가 맞아야 한다.
- time-pressure 종료 판정은 `wall_seconds >= 180`이 아니라 `decision_seconds >= 180` 기준이어야 한다.

## 재빌드 필요

Python loop와 shared trace logger 변경은 Docker image 안에 bake되어야 한다.

```powershell
cd agents
foreach ($d in @("computer_use","openai_computer_use","gemini_computer_use","claude_in_chrome","browser_use","langchain_playwright")) {
  Push-Location $d ; docker compose build ; Pop-Location
}
```

PowerShell runner 변경은 host 파일이라 즉시 적용된다.
