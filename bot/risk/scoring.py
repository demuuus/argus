"""
Risk scoring model.

risk = (cvss x 10) + (epss_percentile x 1000) + criticality_bonus + kev_bonus

This matches the formula already documented/relied on elsewhere in ARGUS
(Ai/context_builder.py's prioritization header, and the disclaimer text
in every generated PDF report: "Risk scores are computed using CVSS base
score, EPSS probability, asset criticality, and KEV status"). The EPSS
term was already present in the code below; this docstring previously
omitted it, which made this module misleading as the canonical reference
for the formula.
"""

from typing import Optional

_CRITICALITY_BONUS = {
    "Low":      0,
    "Medium":  10,
    "High":    20,
    "Critical": 30,
}

_KEV_BONUS = 50

def calculate_risk(
    cvss: Optional[float] = 0.0,
    criticality: Optional[str] = None,
    kev: bool = False,
    epss_percentile: Optional[float] = 0.0
) -> int:
    """
    Calculate the risk score based on the given parameters.

    :param cvss: CVSS score (default: 0.0)
    :param criticality: Criticality level ("Low", "Medium", "High", "Critical") (default: None)
    :param kev: Flag indicating if the CVE is a KEV (default: False)
    :param epss_percentile: EPSS percentile score (default: 0.0)
    :return: Calculated risk score
    """
    score = int(float(cvss or 0.0) * 10)

    score += int(float(epss_percentile or 0.0) * 1000)

    if kev:
        score += _KEV_BONUS

    score += _CRITICALITY_BONUS.get(
        criticality or "",
        0
    )

    return score