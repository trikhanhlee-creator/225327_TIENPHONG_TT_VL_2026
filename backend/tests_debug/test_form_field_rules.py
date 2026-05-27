from app.services.autofill.form_field_rules import (
    filter_fillable_fields,
    is_signature_footer_field,
    labels_are_equivalent,
)
from app.services.file_parser import FileField


def test_signature_footer_fields():
    assert is_signature_footer_field("dia_diem_viet_don", "Địa điểm viết đơn")
    assert is_signature_footer_field("ngay_viet_don", "Ngày viết đơn")
    assert not is_signature_footer_field("ho_va_ten", "Họ và tên")


def test_label_synonyms():
    assert labels_are_equivalent("Công ty ứng tuyển", "Công ty kính gửi")
    assert labels_are_equivalent("Địa chỉ hiện tại", "Chỗ ở hiện nay")


def test_filter_footer():
    fields = [
        FileField(name="ho_va_ten", label="Họ và tên", field_type="text"),
        FileField(name="ngay_viet_don", label="Ngày viết đơn", field_type="number"),
    ]
    kept = filter_fillable_fields(fields)
    assert len(kept) == 1
    assert kept[0].name == "ho_va_ten"
