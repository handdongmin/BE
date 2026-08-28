package com.likelion.asyncalign.messenger.application;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

import com.likelion.asyncalign.messenger.domain.Conversation;
import com.likelion.asyncalign.messenger.domain.ConversationMember;
import com.likelion.asyncalign.messenger.domain.Message;
import com.likelion.asyncalign.messenger.domain.MessageRepository;
import com.likelion.asyncalign.messenger.dto.MessagePageResponse;
import com.likelion.asyncalign.messenger.dto.MessageResponse;
import com.likelion.asyncalign.user.application.UserService;
import com.likelion.asyncalign.user.domain.User;
import com.likelion.asyncalign.attachment.application.AttachmentService;
import com.likelion.asyncalign.messenger.domain.DeliveryMode;
import com.likelion.asyncalign.messenger.dto.SendMessageRequest;
import com.likelion.asyncalign.global.error.ApiException;
import com.likelion.asyncalign.global.error.ErrorCode;
import com.likelion.asyncalign.alignment.domain.AiReview;
import com.likelion.asyncalign.alignment.domain.UnderstandingCard;
import com.likelion.asyncalign.alignment.domain.UnderstandingCardRepository;
import com.likelion.asyncalign.alignment.application.AiAgentClient;
import com.likelion.asyncalign.messenger.domain.ConversationMemberRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class MessageService {

    private static final Logger log = LoggerFactory.getLogger(MessageService.class);

    private final MessageRepository messageRepository;
    private final ConversationService conversationService;
    private final UserService userService;
    private final AttachmentService attachmentService;
    private final UnderstandingCardRepository cardRepository;
    private final ConversationMemberRepository conversationMemberRepository;
    private final AiAgentClient aiAgentClient;

    public MessageService(
            MessageRepository messageRepository,
            ConversationService conversationService,
            UserService userService,
            AttachmentService attachmentService,
            UnderstandingCardRepository cardRepository,
            ConversationMemberRepository conversationMemberRepository,
            AiAgentClient aiAgentClient
    ) {
        this.messageRepository = messageRepository;
        this.conversationService = conversationService;
        this.userService = userService;
        this.attachmentService = attachmentService;
        this.cardRepository = cardRepository;
        this.conversationMemberRepository = conversationMemberRepository;
        this.aiAgentClient = aiAgentClient;
    }

    public MessagePageResponse getMessages(UUID conversationId, UUID currentUserId, Instant before, int size) {
        conversationService.getMembership(conversationId, currentUserId);
        User viewer = userService.getUser(currentUserId);
        int safeSize = Math.clamp(size, 1, 100);
        Instant cursor = before == null ? Instant.now().plusSeconds(1) : before;
        List<Message> result = messageRepository.findPageBefore(
                conversationId,
                cursor,
                PageRequest.of(0, safeSize + 1));

        boolean hasMore = result.size() > safeSize;
        List<Message> page = new ArrayList<>(result.subList(0, Math.min(result.size(), safeSize)));
        Instant nextBefore = hasMore && !page.isEmpty() ? page.getLast().getCreatedAt() : null;
        Collections.reverse(page);
        List<MessageResponse> responses = page.stream()
                .map(message -> toResponse(message, viewer))
                .toList();
        return new MessagePageResponse(responses, hasMore, nextBefore);
    }

    @Transactional
    public MessageResponse send(UUID conversationId, UUID currentUserId, SendMessageRequest request) {
        ConversationMember membership = conversationService.getMembership(conversationId, currentUserId);
        if (request.deliveryMode() != DeliveryMode.AS_IS) {
            throw new ApiException(
                    ErrorCode.INVALID_REQUEST,
                    "AI 확정 메시지는 AI 검토 전용 전송 API를 사용해야 합니다.");
        }
        String content = request.content() == null ? "" : request.content().trim();
        if (content.isBlank() && request.safeAttachmentIds().isEmpty()) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, "메시지 본문 또는 첨부파일이 필요합니다.");
        }
        if (request.scheduledFor() != null && !request.scheduledFor().isAfter(Instant.now())) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, "예약 전송 시각은 현재보다 이후여야 합니다.");
        }
        User sender = membership.getUser();
        Conversation conversation = membership.getConversation();
        Message message = messageRepository.saveAndFlush(new Message(
                conversation,
                sender,
                content,
                DeliveryMode.AS_IS,
                request.scheduledFor()));
        translateForRecipient(message, sender);
        attachmentService.attachToMessage(
                conversation,
                currentUserId,
                request.safeAttachmentIds(),
                message);
        if (request.scheduledFor() == null) {
            conversation.touch(message.getCreatedAt());
            membership.markRead(message.getCreatedAt());
        }
        return toResponse(message, sender);
    }

    public MessageResponse toResponse(Message message, User viewer) {
        UnderstandingCard card = cardRepository.findByMessageId(message.getId()).orElse(null);
        return MessageResponse.from(
                message,
                viewer,
                attachmentService.getMessageAttachments(message.getId()),
                card == null ? null : new MessageResponse.CardSummary(
                        card.getId(), card.getState().name(), card.getRevisionNumber()));
    }

    @Transactional
    public Message createAiConfirmedMessage(
            ConversationMember membership,
            AiReview review,
            String content,
            Instant scheduledFor
    ) {
        if (scheduledFor != null && !scheduledFor.isAfter(Instant.now())) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, "예약 전송 시각은 현재보다 이후여야 합니다.");
        }
        Message message = messageRepository.saveAndFlush(new Message(
                membership.getConversation(),
                membership.getUser(),
                content,
                DeliveryMode.AI_REVIEW_CONFIRMED,
                scheduledFor));
        message.applyTranslation(
                review.getSourceLanguage(),
                review.getRecipientLanguage(),
                review.getTranslatedContent());
        message.linkAiReview(review);
        attachmentService.attachToMessage(
                membership.getConversation(),
                membership.getUser().getId(),
                review.getAttachments().stream().map(attachment -> attachment.getId()).toList(),
                message);
        if (scheduledFor == null) {
            membership.getConversation().touch(message.getCreatedAt());
            membership.markRead(message.getCreatedAt());
        }
        return message;
    }

    private void translateForRecipient(Message message, User sender) {
        if (message.getContent().isBlank()) {
            return;
        }
        User recipient = conversationMemberRepository.findAllWithUserByConversationId(
                        message.getConversation().getId()).stream()
                .map(ConversationMember::getUser)
                .filter(user -> !user.getId().equals(sender.getId()))
                .findFirst()
                .orElse(null);
        if (recipient == null) {
            return;
        }
        String sourceLanguage = sender.getPreferredLanguage();
        String targetLanguage = recipient.getPreferredLanguage();
        message.applyTranslation(sourceLanguage, targetLanguage, null);
        if (sourceLanguage == null || targetLanguage == null
                || sourceLanguage.equalsIgnoreCase(targetLanguage)
                || !aiAgentClient.isEnabled()) {
            return;
        }
        try {
            AiAgentClient.TranslationResult result = aiAgentClient.translate(
                    new AiAgentClient.TranslationInput(
                            message.getContent(), sourceLanguage, targetLanguage));
            message.applyTranslation(sourceLanguage, targetLanguage, result.translatedContent());
        } catch (AiAgentClient.AiAgentClientException exception) {
            log.warn(
                    "Message translation failed; sending original message. conversationId={}, error={}",
                    message.getConversation().getId(),
                    exception.getMessage());
        }
    }
}
