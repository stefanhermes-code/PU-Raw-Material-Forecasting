"""core market data entities"""
from alembic import op
from app.db.models import Base

revision = "0001_core_entities"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
