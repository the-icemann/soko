from pydantic import BaseModel, field_validator
from typing import Optional, List


class ParticipantOut(BaseModel):
    id:       str
    name:     str
    initials: str
    avatar:   Optional[str] = None


class ConversationOut(BaseModel):
    id:            str
    participant:   ParticipantOut
    lastMessage:   Optional[str]   = None
    lastMessageAt: Optional[str]   = None
    unreadCount:   int
    listingId:     Optional[str]   = None
    listingName:   Optional[str]   = None
    createdAt:     str
    updatedAt:     str


class MessageOut(BaseModel):
    id:             str
    conversationId: str
    senderId:       str
    senderName:     str
    senderInitials: str
    body:           str
    status:         str
    isDeleted:      bool
    isMine:         bool
    createdAt:      str


class StartConversationPayload(BaseModel):
    recipient_id:  str
    listing_id:    Optional[str] = None
    first_message: str

    @field_validator("first_message")
    @classmethod
    def not_empty(cls, v):
        if not v.strip():
            raise ValueError("Message cannot be empty")
        return v.strip()


class SendMessagePayload(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def not_empty(cls, v):
        if not v.strip():
            raise ValueError("Message cannot be empty")
        return v.strip()


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    messages:     List[MessageOut]


class StartConversationOut(BaseModel):
    conversation: ConversationOut
    message:      MessageOut
    isNew:        bool