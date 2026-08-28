package com.likelion.asyncalign.messenger;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.likelion.asyncalign.auth.dto.SignUpRequest;
import com.likelion.asyncalign.alignment.application.AiAgentClient;
import java.time.LocalTime;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import com.likelion.asyncalign.workspace.domain.Workspace;
import com.likelion.asyncalign.workspace.domain.WorkspaceRepository;
import com.likelion.asyncalign.workspace.domain.WorkspaceMember;
import com.likelion.asyncalign.workspace.domain.WorkspaceMemberRepository;
import com.likelion.asyncalign.workspace.domain.WorkspaceRole;
import com.likelion.asyncalign.user.domain.UserRepository;
import com.likelion.asyncalign.user.domain.User;
import com.likelion.asyncalign.user.domain.WorkRole;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class MessengerFlowIntegrationTest {

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    WorkspaceRepository workspaceRepository;

    @Autowired
    WorkspaceMemberRepository workspaceMemberRepository;

    @Autowired
    UserRepository userRepository;

    @MockitoBean
    AiAgentClient aiAgentClient;

    @Test
    void createDirectConversationAndSendMessage() throws Exception {
        Map<String, Object> seoyeon = signUp("sender@example.com", "이서연", "Asia/Seoul");
        Map<String, Object> alex = signUp("receiver@example.com", "Alex", "America/Los_Angeles");
        String senderToken = seoyeon.get("accessToken").toString();
        UUID senderId = UUID.fromString(((Map<?, ?>) seoyeon.get("user")).get("id").toString());
        UUID alexId = UUID.fromString(((Map<?, ?>) alex.get("user")).get("id").toString());
        User sender = userRepository.findById(senderId).orElseThrow();
        sender.updateProfile("이서연", WorkRole.DEVELOPER, null, "ko");
        User recipient = userRepository.findById(alexId).orElseThrow();
        recipient.updateProfile("Alex", WorkRole.PROJECT_MANAGER, null, "en");
        userRepository.saveAllAndFlush(java.util.List.of(sender, recipient));

        String workspaceBody = mockMvc.perform(post("/api/v1/workspaces")
                        .header("Authorization", "Bearer " + senderToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"Messenger Team\",\"organizationDomain\":null}"))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        UUID workspaceId = UUID.fromString(objectMapper.readValue(
                workspaceBody, new TypeReference<Map<String, Object>>() {}).get("id").toString());
        Workspace workspace = workspaceRepository.findById(workspaceId).orElseThrow();
        workspaceMemberRepository.saveAndFlush(new WorkspaceMember(
                workspace, userRepository.findById(alexId).orElseThrow(), WorkspaceRole.MEMBER));

        mockMvc.perform(get("/api/v1/users")
                        .header("Authorization", "Bearer " + senderToken)
                        .param("workspaceId", workspaceId.toString())
                        .param("query", "Alex"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].displayName").value("Alex"))
                .andExpect(jsonPath("$[0].email").doesNotExist())
                .andExpect(jsonPath("$[0].workStart").doesNotExist())
                .andExpect(jsonPath("$[0].emailVerified").doesNotExist());

        String conversationBody = mockMvc.perform(post("/api/v1/conversations/direct")
                        .header("Authorization", "Bearer " + senderToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "workspaceId", workspaceId,
                                "otherUserId", alexId))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.otherParticipant.displayName").value("Alex"))
                .andExpect(jsonPath("$.otherParticipant.email").doesNotExist())
                .andExpect(jsonPath("$.otherParticipant.workStart").doesNotExist())
                .andReturn().getResponse().getContentAsString();
        UUID conversationId = UUID.fromString(
                objectMapper.readValue(conversationBody, new TypeReference<Map<String, Object>>() {}).get("id").toString());

        when(aiAgentClient.isEnabled()).thenReturn(true);
        when(aiAgentClient.translate(any())).thenReturn(new AiAgentClient.TranslationResult(
                "Please review it by tomorrow.", "ko", "en"));

        mockMvc.perform(post("/api/v1/conversations/{id}/messages", conversationId)
                        .header("Authorization", "Bearer " + senderToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "content", "내일까지 검토 부탁드려요.",
                                "attachmentIds", java.util.List.of(),
                                "deliveryMode", "AS_IS"))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.sourceLanguage").value("ko"))
                .andExpect(jsonPath("$.targetLanguage").value("en"))
                .andExpect(jsonPath("$.translatedContent").value("Please review it by tomorrow."))
                .andExpect(jsonPath("$.senderLocalSentAt").exists())
                .andExpect(jsonPath("$.viewerLocalSentAt").exists());

        mockMvc.perform(get("/api/v1/conversations/{id}/messages", conversationId)
                        .header("Authorization", "Bearer " + senderToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.messages[0].content").value("내일까지 검토 부탁드려요."))
                .andExpect(jsonPath("$.messages[0].translatedContent")
                        .value("Please review it by tomorrow."));

        when(aiAgentClient.translate(any())).thenThrow(
                new AiAgentClient.AiAgentClientException(502, "AI unavailable"));
        mockMvc.perform(post("/api/v1/conversations/{id}/messages", conversationId)
                        .header("Authorization", "Bearer " + senderToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "content", "번역 실패여도 전송됩니다.",
                                "attachmentIds", java.util.List.of(),
                                "deliveryMode", "AS_IS"))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.content").value("번역 실패여도 전송됩니다."))
                .andExpect(jsonPath("$.sourceLanguage").value("ko"))
                .andExpect(jsonPath("$.targetLanguage").value("en"));
    }

    private Map<String, Object> signUp(String email, String name, String timeZone) throws Exception {
        SignUpRequest request = new SignUpRequest(
                email,
                "password123!",
                name,
                null,
                true);
        String body = mockMvc.perform(post("/api/v1/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readValue(body, new TypeReference<>() {});
    }
}
