from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    forms = relationship("Form", back_populates="user", cascade="all, delete-orphan")
    entries = relationship("Entry", back_populates="user", cascade="all, delete-orphan")
    suggestions = relationship("Suggestion", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    compositions = relationship("CompositionHistory", back_populates="user", cascade="all, delete-orphan")
    word_templates = relationship("WordTemplate", back_populates="user", cascade="all, delete-orphan")
    excel_templates = relationship("ExcelTemplate", back_populates="user", cascade="all, delete-orphan")
    email_verifications = relationship("EmailVerification", back_populates="user", cascade="all, delete-orphan")
    activities = relationship("UserActivity", back_populates="user", cascade="all, delete-orphan")
    subscriptions = relationship("UserSubscription", back_populates="user", cascade="all, delete-orphan")
    payment_orders = relationship("PaymentOrder", back_populates="user", cascade="all, delete-orphan")
    memory_items = relationship("UserMemoryItem", back_populates="user", cascade="all, delete-orphan")
    memory_chunks = relationship("UserMemoryChunk", back_populates="user", cascade="all, delete-orphan")
    autofill_runs = relationship("AutofillRun", back_populates="user", cascade="all, delete-orphan")
    autofill_feedbacks = relationship("AutofillFeedback", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class Form(Base):
    __tablename__ = "forms"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    form_name = Column(String(255), nullable=False)
    description = Column(Text)
    form_type = Column(String(50), default="standard")  # 'standard', 'word', 'excel'
    is_template = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="forms")
    fields = relationship("Field", back_populates="form", cascade="all, delete-orphan")
    entries = relationship("Entry", back_populates="form", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Form(id={self.id}, form_name={self.form_name})>"


class Field(Base):
    __tablename__ = "fields"

    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    field_name = Column(String(255), nullable=False)
    field_type = Column(String(50))
    display_order = Column(Integer)
    is_required = Column(Boolean, default=False)
    validation_rules = Column(Text)  # JSON format
    placeholder = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    form = relationship("Form", back_populates="fields")
    entries = relationship("Entry", back_populates="field", cascade="all, delete-orphan")
    suggestions = relationship("Suggestion", back_populates="field", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Field(id={self.id}, field_name={self.field_name})>"


class Entry(Base):
    """Lịch sử nhập dữ liệu"""
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    field_id = Column(Integer, ForeignKey("fields.id", ondelete="CASCADE"), nullable=False)
    form_id = Column(Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    value = Column(String(1000), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="entries")
    field = relationship("Field", back_populates="entries")
    form = relationship("Form", back_populates="entries")

    def __repr__(self):
        return f"<Entry(id={self.id}, field_id={self.field_id}, value={self.value})>"


class Suggestion(Base):
    """Bảng cache cho gợi ý"""
    __tablename__ = "suggestions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    field_id = Column(Integer, ForeignKey("fields.id", ondelete="CASCADE"), nullable=False)
    suggested_value = Column(String(1000), nullable=False)
    frequency = Column(Integer, default=1)
    ranking = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="suggestions")
    field = relationship("Field", back_populates="suggestions")

    def __repr__(self):
        return f"<Suggestion(id={self.id}, value={self.suggested_value}, frequency={self.frequency})>"


class WordTemplate(Base):
    """Template từ file Word được upload"""
    __tablename__ = "word_templates"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    template_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    original_filename = Column(String(255), nullable=False)
    fields_json = Column(Text)  # JSON string of fields
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="word_templates")
    submissions = relationship("WordSubmission", back_populates="template", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<WordTemplate(id={self.id}, template_name={self.template_name})>"


class WordSubmission(Base):
    """Lịch sử submit form từ template Word"""
    __tablename__ = "word_submissions"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("word_templates.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    submission_data = Column(Text)  # JSON string of submitted data
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    template = relationship("WordTemplate", back_populates="submissions")
    user = relationship("User")

    def __repr__(self):
        return f"<WordSubmission(id={self.id}, template_id={self.template_id})>"


class Document(Base):
    """Tài liệu soạn thảo được lưu"""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)  # Nội dung tài liệu (HTML hoặc plain text)
    description = Column(Text)
    document_type = Column(String(50))  # 'composition', 'template', 'draft'
    is_public = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="documents")
    compositions = relationship("CompositionHistory", back_populates="document", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Document(id={self.id}, title={self.title})>"


class CompositionHistory(Base):
    """Lịch sử AI suggestions và edits trong quá trình soạn thảo"""
    __tablename__ = "composition_history"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action_type = Column(String(50), nullable=False)  # 'suggestion', 'edit', 'acceptance', 'rejection'
    suggested_text = Column(String(500))  # Text được gợi ý
    original_text = Column(String(500))  # Text gốc (trước khi edit)
    modified_text = Column(String(500))  # Text sau khi edit (nếu apply suggestion)
    context = Column(Text)  # Ngữ cảnh để AI tạo suggestion
    accepted = Column(Integer, default=0)  # 1: chấp nhận, 0: từ chối, NULL: chưa quyết định
    ai_model = Column(String(50))  # Model AI được sử dụng (GPT-4, Claude, etc.)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    document = relationship("Document", back_populates="compositions")
    user = relationship("User", back_populates="compositions")

    def __repr__(self):
        return f"<CompositionHistory(id={self.id}, action_type={self.action_type})>"


class ExcelTemplate(Base):
    """Template từ file Excel được upload"""
    __tablename__ = "excel_templates"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    template_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    original_filename = Column(String(255), nullable=False)
    sheet_name = Column(String(255))  # Tên sheet trong Excel
    headers_json = Column(Text)  # JSON danh sách cột headers
    data_row_start = Column(Integer, default=2)  # Hàng bắt đầu có dữ liệu
    mapping_json = Column(Text)  # JSON mapping: column -> field
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="excel_templates")

    def __repr__(self):
        return f"<ExcelTemplate(id={self.id}, template_name={self.template_name})>"


class EmailVerification(Base):
    """Xác thực email khi người dùng đăng ký"""
    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    token = Column(String(500), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="email_verifications")

    def __repr__(self):
        return f"<EmailVerification(id={self.id}, email={self.email}, is_verified={self.is_verified})>"


class UserActivity(Base):
    """Lịch sử sử dụng trang web theo từng tài khoản."""
    __tablename__ = "user_activities"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_type = Column(String(50), nullable=False, index=True)  # login, logout, page_view, feature_access
    feature = Column(String(100), nullable=True, index=True)  # home, composer, excel_upload, ...
    path = Column(String(255), nullable=True)
    method = Column(String(10), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="activities")

    def __repr__(self):
        return f"<UserActivity(id={self.id}, user_id={self.user_id}, feature={self.feature})>"


class AuditLog(Base):
    """Lịch sử hoạt động của admin - tất cả các hành động quản lý"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(100), nullable=False, index=True)  # user_created, user_updated, user_deleted, form_deleted, etc.
    object_type = Column(String(50), nullable=False, index=True)  # 'user', 'form', 'settings', etc.
    object_id = Column(Integer, nullable=True)  # ID của object bị tác động (user_id, form_id, etc.)
    object_name = Column(String(255), nullable=True)  # Tên của object (username, form_name, etc.)
    description = Column(Text, nullable=True)  # Chi tiết mô tả hành động
    old_value = Column(Text, nullable=True)  # Giá trị cũ (JSON format)
    new_value = Column(Text, nullable=True)  # Giá trị mới (JSON format)
    ip_address = Column(String(50), nullable=True)  # IP của admin
    status = Column(String(20), default="success")  # 'success', 'failed'
    error_message = Column(Text, nullable=True)  # Nếu có lỗi
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    admin = relationship("User", foreign_keys=[admin_id])

    def __repr__(self):
        return f"<AuditLog(id={self.id}, action={self.action}, object_type={self.object_type})>"


class UserSubscription(Base):
    """Thông tin gói dịch vụ của người dùng."""
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_code = Column(String(50), nullable=False, index=True)
    plan_name = Column(String(100), nullable=False)
    amount_vnd = Column(Integer, nullable=False, default=0)
    duration_days = Column(Integer, nullable=False, default=30)
    status = Column(String(20), nullable=False, default="active", index=True)  # active | expired | cancelled
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="subscriptions")

    def __repr__(self):
        return f"<UserSubscription(id={self.id}, user_id={self.user_id}, plan_code={self.plan_code}, status={self.status})>"


class PaymentOrder(Base):
    """Đơn hàng thanh toán SePay."""
    __tablename__ = "payment_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    order_code = Column(String(64), unique=True, nullable=False, index=True)
    plan_code = Column(String(50), nullable=False, index=True)
    plan_name = Column(String(100), nullable=False)
    amount_vnd = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="VND")
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending | paid | failed | expired
    sepay_checkout_url = Column(String(500), nullable=True)
    sepay_qr_url = Column(String(500), nullable=True)
    sepay_transaction_id = Column(String(100), nullable=True, index=True)
    customer_note = Column(String(500), nullable=True)
    raw_response = Column(Text, nullable=True)
    paid_at = Column(DateTime, nullable=True, index=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="payment_orders")

    def __repr__(self):
        return f"<PaymentOrder(id={self.id}, order_code={self.order_code}, status={self.status})>"


class FormInstance(Base):
    """One uploaded form instance used by LLM autofill pipeline."""
    __tablename__ = "form_instances"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_type = Column(String(30), nullable=False, index=True)  # word|excel|document
    source_ref = Column(String(255), nullable=False, index=True)  # template_id/session_id/file
    original_filename = Column(String(255), nullable=True)
    schema_version = Column(String(40), nullable=False, default="v1")
    parse_status = Column(String(30), nullable=False, default="parsed")
    parse_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User")
    fields = relationship("FormInstanceField", back_populates="form_instance", cascade="all, delete-orphan")
    runs = relationship("AutofillRun", back_populates="form_instance", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<FormInstance(id={self.id}, source_type={self.source_type}, source_ref={self.source_ref})>"


class FormInstanceField(Base):
    """Canonical field generated by parse + field understanding agents."""
    __tablename__ = "form_instance_fields"

    id = Column(Integer, primary_key=True, index=True)
    form_instance_id = Column(Integer, ForeignKey("form_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    field_key = Column(String(255), nullable=False, index=True)
    field_label = Column(String(255), nullable=False)
    field_type = Column(String(50), nullable=False, default="text")
    aliases_json = Column(Text, nullable=True)
    constraints_json = Column(Text, nullable=True)
    display_order = Column(Integer, nullable=False, default=0)
    is_required = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    form_instance = relationship("FormInstance", back_populates="fields")

    def __repr__(self):
        return f"<FormInstanceField(id={self.id}, field_key={self.field_key}, type={self.field_type})>"


class UserMemoryItem(Base):
    """Unified memory store used by retrieval agent."""
    __tablename__ = "user_memory_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    memory_type = Column(String(30), nullable=False, index=True)  # profile|entry|document|confirmed|behavior
    field_key = Column(String(255), nullable=False, index=True)
    field_type = Column(String(50), nullable=True)
    value_text = Column(String(1000), nullable=False)
    value_json = Column(Text, nullable=True)
    source_ref = Column(String(255), nullable=True, index=True)
    confidence = Column(Float, nullable=False, default=0.7)
    score = Column(Float, nullable=False, default=0.0)
    is_confirmed = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="memory_items")
    chunks = relationship(
        "UserMemoryChunk",
        back_populates="memory_item",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<UserMemoryItem(id={self.id}, memory_type={self.memory_type}, field_key={self.field_key})>"


class UserMemoryChunk(Base):
    """Embedded text chunks for semantic (RAG) retrieval, scoped per user."""

    __tablename__ = "user_memory_chunks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    memory_item_id = Column(
        Integer,
        ForeignKey("user_memory_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    chunk_text = Column(Text, nullable=False)
    embedding_model = Column(String(100), nullable=False)
    embedding_dim = Column(Integer, nullable=False)
    embedding_json = Column(Text, nullable=False)
    source_ref = Column(String(255), nullable=True, index=True)
    field_key = Column(String(255), nullable=True, index=True)
    extra_metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="memory_chunks")
    memory_item = relationship("UserMemoryItem", back_populates="chunks")

    def __repr__(self):
        return f"<UserMemoryChunk(id={self.id}, user_id={self.user_id}, field_key={self.field_key})>"


class AutofillRun(Base):
    """A single AI prefill execution over one form instance."""
    __tablename__ = "autofill_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    form_instance_id = Column(Integer, ForeignKey("form_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="completed", index=True)  # running|completed|failed
    total_fields = Column(Integer, nullable=False, default=0)
    prefilled_fields = Column(Integer, nullable=False, default=0)
    fallback_used = Column(Boolean, nullable=False, default=False)
    latency_ms = Column(Integer, nullable=False, default=0)
    model_name = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = relationship("User", back_populates="autofill_runs")
    form_instance = relationship("FormInstance", back_populates="runs")
    suggestions = relationship("AutofillSuggestion", back_populates="run", cascade="all, delete-orphan")
    feedback_items = relationship("AutofillFeedback", back_populates="run", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<AutofillRun(id={self.id}, status={self.status}, prefilled={self.prefilled_fields})>"


class AutofillSuggestion(Base):
    """Field-level suggestion generated in one autofill run."""
    __tablename__ = "autofill_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("autofill_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    form_field_id = Column(Integer, ForeignKey("form_instance_fields.id", ondelete="CASCADE"), nullable=False, index=True)
    suggested_value = Column(String(1000), nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    reason = Column(Text, nullable=True)
    fallback_used = Column(Boolean, nullable=False, default=False, index=True)
    source_trace_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("AutofillRun", back_populates="suggestions")
    form_field = relationship("FormInstanceField")

    def __repr__(self):
        return f"<AutofillSuggestion(id={self.id}, run_id={self.run_id}, field={self.form_field_id})>"


class AutofillFeedback(Base):
    """User review decision after AI prefill."""
    __tablename__ = "autofill_feedback"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("autofill_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    form_field_id = Column(Integer, ForeignKey("form_instance_fields.id", ondelete="CASCADE"), nullable=False, index=True)
    decision = Column(String(20), nullable=False, index=True)  # accepted|edited|rejected
    suggested_value = Column(String(1000), nullable=True)
    final_value = Column(String(1000), nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    feedback_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    run = relationship("AutofillRun", back_populates="feedback_items")
    user = relationship("User", back_populates="autofill_feedbacks")
    form_field = relationship("FormInstanceField")

    def __repr__(self):
        return f"<AutofillFeedback(id={self.id}, decision={self.decision})>"
