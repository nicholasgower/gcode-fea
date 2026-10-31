"""Solver templates and element-deletion support."""

from .coupon import coupon_node_sets, interface_summary, write_coupon_job
from .element_deletion import (
    CRITERIA,
    PAPER_DELETION_FRACTION,
    DeletionResult,
    DeletionStep,
    criterion_values,
    localize,
    principal_stresses,
    rankine,
    run_deletion_loop,
    von_mises,
    write_deletion_plan,
)
from .inp import write_abaqus, write_calculix

__all__ = [
    "CRITERIA",
    "PAPER_DELETION_FRACTION",
    "DeletionResult",
    "DeletionStep",
    "coupon_node_sets",
    "criterion_values",
    "interface_summary",
    "localize",
    "principal_stresses",
    "rankine",
    "run_deletion_loop",
    "von_mises",
    "write_abaqus",
    "write_calculix",
    "write_coupon_job",
    "write_deletion_plan",
]
