package com.likelion.asyncalign.alignment.application;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

@Component
public class AiAgentClient {

    private final RestClient restClient;
    private final RestClient translationRestClient;
    private final boolean enabled;
    private final String internalApiKey;

    public AiAgentClient(
            RestClient.Builder restClientBuilder,
            @Value("${app.ai.ditto.enabled:false}") boolean enabled,
            @Value("${app.ai.ditto.base-url:http://localhost:8000}") String baseUrl,
            @Value("${app.ai.ditto.internal-api-key:}") String internalApiKey
    ) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(3_000);
        requestFactory.setReadTimeout(70_000);
        this.restClient = restClientBuilder
                .baseUrl(baseUrl)
                .requestFactory(requestFactory)
                .build();
        SimpleClientHttpRequestFactory translationRequestFactory = new SimpleClientHttpRequestFactory();
        translationRequestFactory.setConnectTimeout(3_000);
        translationRequestFactory.setReadTimeout(15_000);
        this.translationRestClient = RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(translationRequestFactory)
                .build();
        this.enabled = enabled;
        this.internalApiKey = internalApiKey;
    }

    public boolean isEnabled() {
        return enabled;
    }

    public SessionResult start(StartInput input) {
        requireEnabled();
        return execute(() -> restClient.post()
                .uri("/internal/v1/sessions")
                .contentType(MediaType.APPLICATION_JSON)
                .headers(headers -> addInternalKey(headers::set))
                .body(input)
                .retrieve()
                .body(SessionResult.class));
    }

    public SessionResult answer(String threadId, String answer) {
        requireEnabled();
        return execute(() -> restClient.post()
                .uri("/internal/v1/sessions/{threadId}/answers", threadId)
                .contentType(MediaType.APPLICATION_JSON)
                .headers(headers -> addInternalKey(headers::set))
                .body(new AnswerInput(answer))
                .retrieve()
                .body(SessionResult.class));
    }

    public TranslationResult translate(TranslationInput input) {
        requireEnabled();
        return executeTranslation(() -> translationRestClient.post()
                .uri("/internal/v1/translations")
                .contentType(MediaType.APPLICATION_JSON)
                .headers(headers -> addInternalKey(headers::set))
                .body(input)
                .retrieve()
                .body(TranslationResult.class));
    }

    private SessionResult execute(ClientCall call) {
        try {
            SessionResult result = call.execute();
            if (result == null) {
                throw new AiAgentClientException(502, "AI service returned an empty response");
            }
            return result;
        } catch (RestClientResponseException exception) {
            throw new AiAgentClientException(
                    exception.getStatusCode().value(),
                    "AI service request failed: " + exception.getResponseBodyAsString(),
                    exception);
        } catch (AiAgentClientException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new AiAgentClientException(502, "AI service is unavailable", exception);
        }
    }

    private TranslationResult executeTranslation(TranslationCall call) {
        try {
            TranslationResult result = call.execute();
            if (result == null || result.translatedContent() == null || result.translatedContent().isBlank()) {
                throw new AiAgentClientException(502, "AI service returned an empty translation");
            }
            if (result.translatedContent().length() > 4000) {
                throw new AiAgentClientException(502, "AI service returned an oversized translation");
            }
            return result;
        } catch (RestClientResponseException exception) {
            throw new AiAgentClientException(
                    exception.getStatusCode().value(),
                    "AI translation request failed: " + exception.getResponseBodyAsString(),
                    exception);
        } catch (AiAgentClientException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new AiAgentClientException(502, "AI translation service is unavailable", exception);
        }
    }

    private void addInternalKey(HeaderSetter setter) {
        if (internalApiKey != null && !internalApiKey.isBlank()) {
            setter.set("X-Internal-Api-Key", internalApiKey);
        }
    }

    private void requireEnabled() {
        if (!enabled) {
            throw new IllegalStateException("Ditto AI service is disabled");
        }
    }

    @FunctionalInterface
    private interface ClientCall {
        SessionResult execute();
    }

    @FunctionalInterface
    private interface TranslationCall {
        TranslationResult execute();
    }

    @FunctionalInterface
    private interface HeaderSetter {
        void set(String name, String value);
    }

    public static class AiAgentClientException extends RuntimeException {
        private final int status;

        public AiAgentClientException(int status, String message) {
            super(message);
            this.status = status;
        }

        public AiAgentClientException(int status, String message, Throwable cause) {
            super(message, cause);
            this.status = status;
        }

        public int status() {
            return status;
        }
    }

    public record StartInput(
            @JsonProperty("review_id") String reviewId,
            String draft,
            UserContext sender,
            UserContext receiver,
            @JsonProperty("receiver_work") WorkContext receiverWork,
            @JsonProperty("recent_messages") List<String> recentMessages,
            List<AttachmentContext> attachments
    ) {
    }

    public record UserContext(
            @JsonProperty("user_id") String userId,
            String name,
            @JsonProperty("time_zone") String timeZone,
            String language
    ) {
    }

    public record WorkContext(String start, String end, List<String> days) {
    }

    public record AttachmentContext(
            @JsonProperty("attachment_id") String attachmentId,
            @JsonProperty("file_name") String fileName,
            @JsonProperty("extracted_text") String extractedText
    ) {
    }

    private record AnswerInput(String answer) {
    }

    public record TranslationInput(
            String content,
            @JsonProperty("source_language") String sourceLanguage,
            @JsonProperty("target_language") String targetLanguage
    ) {
    }

    public record TranslationResult(
            @JsonProperty("translated_content") String translatedContent,
            @JsonProperty("source_language") String sourceLanguage,
            @JsonProperty("target_language") String targetLanguage
    ) {
    }

    public record SessionResult(
            @JsonProperty("thread_id") String threadId,
            String status,
            InterruptPayload interrupt,
            ConfirmedCard card
    ) {
        public boolean interrupted() {
            return "interrupt".equalsIgnoreCase(status);
        }

        public boolean done() {
            return "done".equalsIgnoreCase(status);
        }
    }

    public record InterruptPayload(int step, int total, AmbiguityItem item) {
    }

    public record AmbiguityItem(
            String span,
            String category,
            String reason,
            List<String> candidates,
            String suggestion
    ) {
    }

    public record ConfirmedCard(
            String task,
            String assignee,
            @JsonProperty("deadline_confirmed") String deadlineConfirmed,
            @JsonProperty("deadline_receiver_local") String deadlineReceiverLocal,
            @JsonProperty("request_type") String requestType,
            @JsonProperty("decision_status") String decisionStatus,
            @JsonProperty("expected_outcome") String expectedOutcome,
            @JsonProperty("interpretation_note") String interpretationNote,
            List<String> notes,
            Conflict conflict,
            String evidence
    ) {
    }

    public record Conflict(
            @JsonProperty("receiver_local_time") String receiverLocalTime,
            @JsonProperty("within_working_hours") boolean withinWorkingHours,
            String note
    ) {
    }
}
