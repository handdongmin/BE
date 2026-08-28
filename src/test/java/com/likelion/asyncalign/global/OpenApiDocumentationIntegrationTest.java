package com.likelion.asyncalign.global;

import static org.hamcrest.Matchers.startsWith;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class OpenApiDocumentationIntegrationTest {

    @Autowired
    MockMvc mockMvc;

    @MockitoBean
    JavaMailSender mailSender;

    @Test
    void exposesOpenApi30DocumentWithoutAuthentication() throws Exception {
        mockMvc.perform(get("/v3/api-docs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.openapi", startsWith("3.0.")))
                .andExpect(jsonPath("$.info.title").value("ditto API"))
                .andExpect(jsonPath("$.info.version").value("1.0.0"))
                .andExpect(jsonPath("$.components.securitySchemes.bearerAuth").exists())
                .andExpect(jsonPath("$.components.schemas.MessageResponse.properties.translatedContent").exists())
                .andExpect(jsonPath("$.components.schemas.MessageResponse.properties.sourceLanguage").exists())
                .andExpect(jsonPath("$.components.schemas.MessageResponse.properties.targetLanguage").exists())
                .andExpect(jsonPath("$.paths['/api/v1/auth/login'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/conversations/{conversationId}/messages'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces/{workspaceId}'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces/{workspaceId}'].delete").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces/{workspaceId}/members'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces/{workspaceId}/members/me/work-context'].put").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces/{workspaceId}/members/me/work-context'].delete").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces/{workspaceId}/invitations'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspaces/{workspaceId}/invitation-links'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspace-invitations/{token}'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/workspace-invitations/{token}/accept'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/conversations/{conversationId}/attachments'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/attachments/{attachmentId}'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/attachments/{attachmentId}/content'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/conversations/{conversationId}/ai-reviews'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/ai-reviews/{reviewId}'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/ai-reviews/{reviewId}'].patch").exists())
                .andExpect(jsonPath("$.paths['/api/v1/ai-reviews/{reviewId}/answers'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/ai-reviews/{reviewId}/send'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/messages/{messageId}/understanding-cards'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/understanding-cards/{cardId}'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/understanding-cards/{cardId}/responses'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/understanding-cards/{cardId}/revisions'].post").exists())
                .andExpect(jsonPath("$.paths['/api/v1/conversations/{conversationId}/agreement-logs'].get").exists())
                .andExpect(jsonPath("$.paths['/api/v1/auth/oauth/exchange']").doesNotExist())
                .andExpect(jsonPath("$.paths['/api/v1/calendar/connection']").doesNotExist());
    }

    @Test
    void exposesSwaggerUiWithoutAuthentication() throws Exception {
        mockMvc.perform(get("/swagger-ui.html"))
                .andExpect(status().is3xxRedirection());
    }

    @Test
    void returnsCommonErrorBodyForMissingOrInvalidJwt() throws Exception {
        mockMvc.perform(get("/api/v1/users/me"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().contentTypeCompatibleWith("application/json"))
                .andExpect(jsonPath("$.code").value("INVALID_CREDENTIALS"))
                .andExpect(jsonPath("$.fieldErrors").isMap());

        mockMvc.perform(get("/api/v1/users/me")
                        .header("Authorization", "Bearer invalid-token"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().contentTypeCompatibleWith("application/json"))
                .andExpect(jsonPath("$.code").value("INVALID_CREDENTIALS"));
    }
}
