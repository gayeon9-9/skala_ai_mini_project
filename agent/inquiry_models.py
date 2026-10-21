"""접수 저장소의 공통 상태. 'completed'는 답변 저장 완료이지 장애 해결이 아니다."""

from enum import Enum


class RequestStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class InquiryConflict(ValueError):
    """다른 시스템·본문 재사용·동시 처리를 HTTP 409로 안내하기 위한 예외."""
