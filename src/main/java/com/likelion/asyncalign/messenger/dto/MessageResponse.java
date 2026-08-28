package com.likelion.asyncalign.messenger.dto;

import java.time.Instant;
import java.time.ZonedDateTime;
import java.util.UUID;

import com.likelion.asyncalign.messenger.domain.Message;
import com.likelion.asyncalign.user.domain.User;
import com.likelion.asyncalign.attachment.dto.AttachmentResponse;
import com.likelion.asyncalign.messenger.domain.ConfirmationStatus;
import com.likelion.asyncalign.messenger.domain.DeliveryMode;
import com.likelion.asyncalign.messenger.domain.DeliveryStatus;
import java.util.List;

public record MessageResponse(
        UUID id,
        UUID conversationId,
        Sender sender,
        String content,
        String sourceLanguage,
        String targetLanguage,
        String translatedContent,
        DeliveryMode deliveryMode,
        DeliveryStatus deliveryStatus,
        ConfirmationStatus confirmationStatus,
        List<AttachmentResponse> attachments,
        CardSummary understandingCard,
        Instant scheduledFor,
        Instant sentAt,
        ZonedDateTime senderLocalSentAt,
        ZonedDateTime viewerLocalSentAt
) {
    public static MessageResponse from(
            Message message,
            User viewer,
            List<AttachmentResponse> attachments,
            CardSummary understandingCard
    ) {
        User sender = message.getSender();
        Instant sentAt = message.getCreatedAt();
        return new MessageResponse(
                message.getId(),
                message.getConversation().getId(),
                new Sender(sender.getId(), sender.getDisplayName(), sender.getTimeZoneId()),
                message.getContent(),
                message.getSourceLanguage(),
                message.getTargetLanguage(),
                message.getTranslatedContent(),
                message.getDeliveryMode(),
                message.getDeliveryStatus(),
                message.getConfirmationStatus(),
                attachments,
                understandingCard,
                message.getScheduledFor(),
                sentAt,
                sentAt.atZone(java.time.ZoneId.of(sender.getTimeZoneId())),
                sentAt.atZone(java.time.ZoneId.of(viewer.getTimeZoneId())));
    }

    public record Sender(UUID id, String displayName, String timeZoneId) {
    }

    public record CardSummary(UUID id, String state, int revision) {
    }
}
