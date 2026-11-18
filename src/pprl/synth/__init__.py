"""Generate synthetic multi-site data: OMOP CDM 5.4 tables and pprl patient files.

A scenario YAML describes the sites, the individuals, and what each site
records about them. ``synthesize`` writes the dataset.
"""

from .generate import STEPS, synthesize

__all__ = ["STEPS", "synthesize"]
