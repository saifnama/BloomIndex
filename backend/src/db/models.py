"""SQLAlchemy models for the two-table schema (papers + paper_entities).

The database schema provides an annotation layer over scientific papers:

    papers: one row per paper containing DOI and display metadata.
    paper_entities: one row per (paper, label, canonical_text) fact.

Full text is fetched dynamically from upstream providers at view time,
while entity dictionary enrichment is applied at render time. The
metadata column on paper_entities stores optional structured ingestion
payloads (such as taxonomic family or geographic location).

Python attribute naming:
    The SQL column "metadata" is mapped to the Python attribute "meta"
    to prevent shadowing the reserved Base.metadata attribute on
    SQLAlchemy declarative classes.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.src.db.session import Base


class Paper(Base):
    """Scientific paper record indexed by unique DOI.

    Maintains core bibliographic metadata and links to extracted
    entities.
    """

    __tablename__ = "papers"

    id = Column(Integer, primary_key=True)
    doi = Column(String, nullable=False, unique=True, index=True)
    title = Column(String)
    journal = Column(String, index=True)
    year = Column(Integer, index=True)
    is_open_access = Column(Boolean)
    # Denormalized entity count updated explicitly during ingestion
    # to avoid expensive table joins on list and summary queries.
    entity_count = Column(Integer)

    paper_entities = relationship(
        "PaperEntity",
        back_populates="paper",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PaperEntity(Base):
    """Annotated entity occurrence associated with a scientific paper.

    Represents an extracted chemical, species, or related biological
    term linked to its parent paper record.
    """

    __tablename__ = "paper_entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(
        Integer,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = Column(String, nullable=False, index=True)
    canonical_text = Column(String, nullable=False)
    frequency = Column(Integer)
    # The SQL column is named "metadata" on disk but mapped to "meta" in
    # Python to avoid shadowing SQLAlchemy's Base.metadata property.
    meta = Column("metadata", JSON)

    paper = relationship("Paper", back_populates="paper_entities")

    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "label",
            "canonical_text",
            name="idx_paper_entities_uniq",
        ),
        CheckConstraint(
            "metadata IS NULL OR json_valid(metadata)",
            name="paper_entities_metadata_check",
        ),
    )
