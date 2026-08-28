package com.likelion.asyncalign.messenger.domain;

import java.util.UUID;

import com.likelion.asyncalign.global.persistence.BaseEntity;
import com.likelion.asyncalign.user.domain.User;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import java.time.Instant;
import com.likelion.asyncalign.alignment.domain.AiReview;

@Entity
@Table(name = "messages")
public class Message extends BaseEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "conversation_id", nullable = false)
    private Conversation conversation;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "sender_id", nullable = false)
    private User sender;

    @Column(nullable = false, length = 4000)
    private String content;

    @Column(length = 10)
    private String sourceLanguage;

    @Column(length = 10)
    private String targetLanguage;

    @Column(length = 4000)
    private String translatedContent;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 30)
    private DeliveryMode deliveryMode = DeliveryMode.AS_IS;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private DeliveryStatus deliveryStatus = DeliveryStatus.SENT;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private ConfirmationStatus confirmationStatus = ConfirmationStatus.UNCONFIRMED;

    private Instant scheduledFor;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "ai_review_id")
    private AiReview aiReview;

    protected Message() {
    }

    public Message(Conversation conversation, User sender, String content) {
        this(conversation, sender, content, DeliveryMode.AS_IS, null);
    }

    public Message(
            Conversation conversation,
            User sender,
            String content,
            DeliveryMode deliveryMode,
            Instant scheduledFor
    ) {
        this.conversation = conversation;
        this.sender = sender;
        this.content = content;
        this.deliveryMode = deliveryMode;
        this.scheduledFor = scheduledFor;
        this.deliveryStatus = scheduledFor == null ? DeliveryStatus.SENT : DeliveryStatus.SCHEDULED;
        this.confirmationStatus = deliveryMode == DeliveryMode.AI_REVIEW_CONFIRMED
                ? ConfirmationStatus.REVIEW
                : ConfirmationStatus.UNCONFIRMED;
    }

    public UUID getId() {
        return id;
    }

    public Conversation getConversation() {
        return conversation;
    }

    public User getSender() {
        return sender;
    }

    public String getContent() {
        return content;
    }

    public DeliveryMode getDeliveryMode() { return deliveryMode; }
    public DeliveryStatus getDeliveryStatus() { return deliveryStatus; }
    public ConfirmationStatus getConfirmationStatus() { return confirmationStatus; }
    public Instant getScheduledFor() { return scheduledFor; }

    public String getSourceLanguage() { return sourceLanguage; }
    public String getTargetLanguage() { return targetLanguage; }
    public String getTranslatedContent() { return translatedContent; }

    public void applyTranslation(String sourceLanguage, String targetLanguage, String translatedContent) {
        this.sourceLanguage = sourceLanguage;
        this.targetLanguage = targetLanguage;
        this.translatedContent = translatedContent;
    }

    public void markDueAsSent() {
        if (deliveryStatus == DeliveryStatus.SCHEDULED) {
            deliveryStatus = DeliveryStatus.SENT;
        }
    }

    public void updateConfirmationStatus(ConfirmationStatus confirmationStatus) {
        this.confirmationStatus = confirmationStatus;
    }

    public void linkAiReview(AiReview aiReview) {
        this.aiReview = aiReview;
    }

    public AiReview getAiReview() { return aiReview; }
}
