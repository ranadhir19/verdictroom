"""The debate protocol's prompts — the original design of verdictroom.

Four moves, in order:

  opening    Each debater, independently and without seeing the other,
             picks the option it judges best, argues from cited
             context evidence, names the risks of its own choice, and
             states the assumption its case rests on.
  crossexam  Each debater reads every opening. It must identify the
             strongest opposing argument, attack it, concede what it
             cannot answer, and state — in advance — what evidence
             would change its mind.
  revision   After cross-examination, each debater restates its final
             position and, if it moved, names the exact argument that
             moved it.
  judge      A model that did not debate weighs the whole transcript,
             decides, preserves the losing side's final argument as
             verbatim dissent, and states what evidence would overturn
             its own decision.

Every prompt demands a single JSON object and forbids inventing facts
the context does not contain: where the context is silent, the model
must say so. The [PHASE: ...] / [LENS: ...] tags are machine markers
the offline fake provider keys on; live models are told to ignore them.
"""

from __future__ import annotations

_RULES = (
    "Rules: argue only from the brief and the context documents. Cite "
    "documents as [D1], [D2] where you rely on them. Where the context "
    "is silent on a point, say 'the context is silent on this' instead "
    "of inventing facts. Return ONLY one JSON object, no prose outside "
    "it, no code fences."
)


def system_for(role: str, lens_name: str = "", lens_text: str = "") -> str:
    base = (
        "You are taking part in a structured decision debate run by "
        "verdictroom. The transcript of this debate becomes a permanent, "
        "auditable decision record, so precision matters more than "
        "winning: a conceded point is recorded as a strength, and a "
        "claim without evidence is recorded as a weakness.")
    if role == "debater":
        return (base + f"\nYour lens for this debate is {lens_name}: "
                f"{lens_text} Argue through that lens, but decide on the "
                "whole brief.")
    if role == "judge":
        return (base + "\nYou are the judge. You did not debate. Your "
                "job is to decide which option the evidence and argument "
                "actually support — not to average the debaters, and not "
                "to reward confidence over substance.")
    return base


def baseline_user(brief_tx: str, context_tx: str) -> str:
    return (f"[PHASE: baseline]\n{brief_tx}\n\nCONTEXT DOCUMENTS:\n"
            f"{context_tx}\n\n{_RULES}\n"
            'Return JSON: {"choice": "<option id>", "rationale": '
            '"<at most 150 words: the decisive reasons>"}')


def opening_user(brief_tx: str, context_tx: str, lens_name: str) -> str:
    return (f"[PHASE: opening] [LENS: {lens_name}]\n{brief_tx}\n\n"
            f"CONTEXT DOCUMENTS:\n{context_tx}\n\n"
            "You have not seen any other debater's position. Choose the "
            f"option you judge best, arguing through your lens ({lens_name}).\n"
            f"{_RULES}\n"
            'Return JSON: {"choice": "<option id>", "ranking": '
            '["<ids best to worst>"], "argument": "<your case, 150-250 '
            'words>", "evidence": [{"claim": "<claim>", "cite": '
            '["D1"]}], "risks": ["<risk of your own choice>"], '
            '"key_assumption": "<the assumption your case rests on>", '
            '"confidence": "low|medium|high"}')


def crossexam_user(brief_tx: str, openings_tx: str, lens_name: str,
                   own_choice: str) -> str:
    return (f"[PHASE: crossexam] [LENS: {lens_name}]\n{brief_tx}\n\n"
            f"ALL OPENING POSITIONS:\n{openings_tx}\n\n"
            f"Your opening choice was {own_choice}. Cross-examine now. "
            "Identify the strongest argument made against your position "
            "(if every debater chose as you did, take the strongest case "
            "for the leading alternative, and the weakest point of the "
            "shared position). Attack it on evidence, not rhetoric. "
            "Concede the points you cannot answer. Then state precisely "
            "what evidence, if it existed, would change your mind — "
            "this is recorded, and the judge will hold you to it.\n"
            f"{_RULES}\n"
            'Return JSON: {"strongest_opposing": {"debater": "<name>", '
            '"argument_summary": "<their argument, fairly stated>"}, '
            '"attack": "<your attack, 100-200 words>", "concessions": '
            '["<point you concede>"], "would_change_my_mind": "<the '
            'evidence that would move you>", "questions_for_others": '
            '["<question>"]}')


def revision_user(brief_tx: str, transcript_tx: str, lens_name: str,
                  opening_choice: str) -> str:
    return (f"[PHASE: revision] [LENS: {lens_name}]\n{brief_tx}\n\n"
            f"DEBATE SO FAR:\n{transcript_tx}\n\n"
            f"Your opening choice was {opening_choice}. Give your final "
            "position. If cross-examination moved you, say so plainly "
            "and name the exact argument that did it — changing your "
            "mind for a stated reason is a success of this process, "
            "not a defeat. If you did not move, name the attack you "
            "found weakest and why it failed.\n"
            f"{_RULES}\n"
            'Return JSON: {"choice": "<option id>", "changed": '
            '<true|false>, "what_changed_it": "<the argument that moved '
            'you, or empty>", "final_argument": "<your closing case, '
            '120-200 words>", "residual_risks": ["<risk that remains '
            'even if you are right>"]}')


def judge_user(brief_tx: str, transcript_tx: str) -> str:
    return (f"[PHASE: judge]\n{brief_tx}\n\nFULL DEBATE TRANSCRIPT:\n"
            f"{transcript_tx}\n\n"
            "Decide. Weigh which arguments survived cross-examination, "
            "which concessions were made, and whether the stated "
            "'would change my mind' conditions were met by anything in "
            "the record. Do not split the difference. In your rationale, "
            "name the specific cross-examination points you relied on. "
            "Preserve the dissent: quote, verbatim, a passage from the "
            "final argument of the debater whose final position lost; "
            "if no debater dissents from your decision, quote the "
            "strongest residual risk raised against it instead and say "
            "so. Finally, state what evidence would overturn your "
            f"decision.\n{_RULES}\n"
            'Return JSON: {"decision": "<option id>", "rationale": '
            '"<200-300 words>", "cross_examination_relied_on": '
            '[{"by": "<debater>", "point": "<the point>"}], "dissent": '
            '{"debater": "<name>", "quote": "<verbatim passage>"}, '
            '"confidence": {"level": "low|medium|high", "why": '
            '"<reason>"}, "overturn_conditions": ["<evidence that '
            'would flip this decision>"], "unresolved_questions": '
            '["<question the debate left open>"]}')
