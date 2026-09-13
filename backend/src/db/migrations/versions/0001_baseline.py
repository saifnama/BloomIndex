"""Initial database schema migration for papers and extracted entities.

Creates baseline relational tables for paper metadata and associated
named entities with index and foreign key constraints.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create papers and paper_entities tables and indexes."""
    op.create_table(
        "papers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("doi", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("journal", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("is_open_access", sa.Boolean(), nullable=True),
        sa.Column("entity_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doi"),
    )
    op.create_index("ix_papers_doi", "papers", ["doi"], unique=True)
    op.create_index("ix_papers_journal", "papers", ["journal"], unique=False)
    op.create_index("ix_papers_year", "papers", ["year"], unique=False)

    op.create_table(
        "paper_entities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("canonical_text", sa.String(), nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Prevent duplicate entity classifications per paper.
        sa.UniqueConstraint("paper_id", "label", "canonical_text", name="idx_paper_entities_uniq"),
        # Ensure SQLite validates JSON formatting when metadata is set.
        sa.CheckConstraint(
            "metadata IS NULL OR json_valid(metadata)",
            name="paper_entities_metadata_check",
        ),
    )
    op.create_index("ix_paper_entities_paper_id", "paper_entities", ["paper_id"], unique=False)
    op.create_index("ix_paper_entities_label", "paper_entities", ["label"], unique=False)


def downgrade() -> None:
    """Drop paper_entities and papers tables."""
    op.drop_table("paper_entities")
    op.drop_table("papers")
