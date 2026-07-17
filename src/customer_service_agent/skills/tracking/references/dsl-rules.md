# Dify source rules

- Original scenario: `track`; default mock function: `query_track`.
- Missing `waybill_no` causes a three-language clarification and waits for the next turn.
- Mock status depends on the last digit: 0–2 in transit, 3–4 out for delivery, 5–6 delivered, 7–8 delayed, 9 exception.
- The original final prompt prohibits inventing facts or promising delivery today.

