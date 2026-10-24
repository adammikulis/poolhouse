// Generated proposal. No dispatch or authority is implemented. Schema a440d9df27bba17919ad222e49b6c8ff981e177dc07cccc08e668c8b1696c64b
use super::contract::*;

pub trait Operation { type Request; type Response; type Error; const NAME: &'static str; }

pub struct JobsGet;
impl Operation for JobsGet { type Request = JobGetRequest; type Response = JobView; type Error = JobError; const NAME: &'static str = "jobs.get"; }

pub struct JobsCancel;
impl Operation for JobsCancel { type Request = JobCancelRequest; type Response = JobView; type Error = JobError; const NAME: &'static str = "jobs.cancel"; }

pub struct JobsList;
impl Operation for JobsList { type Request = JobListRequest; type Response = JobListResponse; type Error = JobError; const NAME: &'static str = "jobs.list"; }

pub struct JobsOutputRead;
impl Operation for JobsOutputRead { type Request = JobOutputRequest; type Response = JobOutputChunk; type Error = JobError; const NAME: &'static str = "jobs.output.read"; }

pub struct AgentsGet;
impl Operation for AgentsGet { type Request = AgentGetRequest; type Response = AgentControlSnapshot; type Error = AgentControlError; const NAME: &'static str = "agents.get"; }

pub struct AgentsPause;
impl Operation for AgentsPause { type Request = AgentControlRequest; type Response = AgentPauseResult; type Error = AgentControlError; const NAME: &'static str = "agents.pause"; }

pub struct AgentsResume;
impl Operation for AgentsResume { type Request = AgentControlRequest; type Response = AgentPauseResult; type Error = AgentControlError; const NAME: &'static str = "agents.resume"; }

pub struct AgentsStop;
impl Operation for AgentsStop { type Request = AgentControlRequest; type Response = AgentStopResult; type Error = AgentControlError; const NAME: &'static str = "agents.stop"; }

pub struct AgentsTaskStart;
impl Operation for AgentsTaskStart { type Request = AgentTaskStartRequest; type Response = AgentTaskStartResult; type Error = AgentControlError; const NAME: &'static str = "agents.task.start"; }

pub struct AgentsTaskStop;
impl Operation for AgentsTaskStop { type Request = AgentTaskStopRequest; type Response = AgentTaskStopResult; type Error = AgentControlError; const NAME: &'static str = "agents.task.stop"; }

pub struct ApprovalsRequest;
impl Operation for ApprovalsRequest { type Request = ApprovalRequest; type Response = ApprovalRequestResult; type Error = ApprovalError; const NAME: &'static str = "approvals.request"; }

pub struct ApprovalsInspect;
impl Operation for ApprovalsInspect { type Request = ApprovalInspectRequest; type Response = ApprovalView; type Error = ApprovalError; const NAME: &'static str = "approvals.inspect"; }

pub struct ApprovalsCancel;
impl Operation for ApprovalsCancel { type Request = ApprovalCancelRequest; type Response = ApprovalCancelResult; type Error = ApprovalError; const NAME: &'static str = "approvals.cancel"; }

pub struct ApprovalsWatch;
impl Operation for ApprovalsWatch { type Request = ApprovalWatchRequest; type Response = ApprovalWatchEvent; type Error = ApprovalError; const NAME: &'static str = "approvals.watch"; }

pub struct GovernanceInspect;
impl Operation for GovernanceInspect { type Request = ApprovalGovernanceInspectRequest; type Response = ApprovalGovernanceView; type Error = ApprovalError; const NAME: &'static str = "governance.inspect"; }

pub struct GovernanceWatch;
impl Operation for GovernanceWatch { type Request = ApprovalGovernanceWatchRequest; type Response = ApprovalGovernanceWatchEvent; type Error = ApprovalError; const NAME: &'static str = "governance.watch"; }

pub struct BoardContentBoardMessagesPost;
impl Operation for BoardContentBoardMessagesPost { type Request = BoardContentPostRequest; type Response = BoardContentPostResult; type Error = BoardContentError; const NAME: &'static str = "board.messages.post"; }

pub struct BoardContentBoardNotesPost;
impl Operation for BoardContentBoardNotesPost { type Request = BoardContentNotePostRequest; type Response = BoardContentPostResult; type Error = BoardContentError; const NAME: &'static str = "board.notes.post"; }

pub struct BoardContentBoardNotesList;
impl Operation for BoardContentBoardNotesList { type Request = BoardContentNoteListRequest; type Response = BoardContentNotePage; type Error = BoardContentError; const NAME: &'static str = "board.notes.list"; }

pub struct BoardContentBoardNotesVerificationRecord;
impl Operation for BoardContentBoardNotesVerificationRecord { type Request = BoardContentVerificationRequest; type Response = BoardContentNoteResult; type Error = BoardContentError; const NAME: &'static str = "board.notes.verification.record"; }

pub struct BoardContentBoardEntriesRead;
impl Operation for BoardContentBoardEntriesRead { type Request = BoardContentReadRequest; type Response = BoardContentEntryPage; type Error = BoardContentError; const NAME: &'static str = "board.entries.read"; }

pub struct BoardContentBoardEntriesDocumentRead;
impl Operation for BoardContentBoardEntriesDocumentRead { type Request = BoardContentDocumentReadRequest; type Response = BoardContentDocumentChunk; type Error = BoardContentError; const NAME: &'static str = "board.entries.document.read"; }

pub struct BoardContentBoardEntriesWatch;
impl Operation for BoardContentBoardEntriesWatch { type Request = BoardContentWatchRequest; type Response = BoardContentWatchPage; type Error = BoardContentError; const NAME: &'static str = "board.entries.watch"; }

pub struct BoardContentBoardOutboxList;
impl Operation for BoardContentBoardOutboxList { type Request = BoardContentOutboxRequest; type Response = BoardContentOutboxPage; type Error = BoardContentError; const NAME: &'static str = "board.outbox.list"; }

pub struct BoardContentBoardRoomsList;
impl Operation for BoardContentBoardRoomsList { type Request = BoardContentRoomListRequest; type Response = BoardContentRoomPage; type Error = BoardContentError; const NAME: &'static str = "board.rooms.list"; }

pub struct BoardContentBoardRoomsCreate;
impl Operation for BoardContentBoardRoomsCreate { type Request = BoardContentRoomCreateRequest; type Response = BoardContentRoomResult; type Error = BoardContentError; const NAME: &'static str = "board.rooms.create"; }

pub struct BoardContentBoardRoomsJoin;
impl Operation for BoardContentBoardRoomsJoin { type Request = BoardContentRoomJoinRequest; type Response = BoardContentRoomResult; type Error = BoardContentError; const NAME: &'static str = "board.rooms.join"; }

pub struct BoardContentBoardRoomsLeave;
impl Operation for BoardContentBoardRoomsLeave { type Request = BoardContentRoomLeaveRequest; type Response = BoardContentRoomResult; type Error = BoardContentError; const NAME: &'static str = "board.rooms.leave"; }

pub struct BoardContentBoardRoomsMemberAdd;
impl Operation for BoardContentBoardRoomsMemberAdd { type Request = BoardContentRoomMemberAddRequest; type Response = BoardContentRoomResult; type Error = BoardContentError; const NAME: &'static str = "board.rooms.member.add"; }

pub struct BoardContentBoardFilesUploadStart;
impl Operation for BoardContentBoardFilesUploadStart { type Request = BoardContentUploadStartRequest; type Response = BoardContentUpload; type Error = BoardContentError; const NAME: &'static str = "board.files.upload.start"; }

pub struct BoardContentBoardFilesUploadAppend;
impl Operation for BoardContentBoardFilesUploadAppend { type Request = BoardContentUploadAppendRequest; type Response = BoardContentUpload; type Error = BoardContentError; const NAME: &'static str = "board.files.upload.append"; }

pub struct BoardContentBoardFilesUploadCancel;
impl Operation for BoardContentBoardFilesUploadCancel { type Request = BoardContentUploadCancelRequest; type Response = BoardContentUpload; type Error = BoardContentError; const NAME: &'static str = "board.files.upload.cancel"; }

pub struct BoardContentBoardFilesRead;
impl Operation for BoardContentBoardFilesRead { type Request = BoardContentFileReadRequest; type Response = BoardContentFileChunk; type Error = BoardContentError; const NAME: &'static str = "board.files.read"; }

pub struct BoardContentBoardFilesDelete;
impl Operation for BoardContentBoardFilesDelete { type Request = BoardContentFileDeleteRequest; type Response = BoardContentDeleteResult; type Error = BoardContentError; const NAME: &'static str = "board.files.delete"; }

pub struct BoardContentBoardDeliverySubscribe;
impl Operation for BoardContentBoardDeliverySubscribe { type Request = BoardContentSubscribeRequest; type Response = BoardContentDeliveryResult; type Error = BoardContentError; const NAME: &'static str = "board.delivery.subscribe"; }

pub struct BoardContentBoardDeliveryUnsubscribe;
impl Operation for BoardContentBoardDeliveryUnsubscribe { type Request = BoardContentUnsubscribeRequest; type Response = BoardContentUnsubscribeResult; type Error = BoardContentError; const NAME: &'static str = "board.delivery.unsubscribe"; }

pub struct BoardContentBoardDeliveryList;
impl Operation for BoardContentBoardDeliveryList { type Request = BoardContentDeliveryListRequest; type Response = BoardContentDeliveryPage; type Error = BoardContentError; const NAME: &'static str = "board.delivery.list"; }

pub struct BoardContentBoardDeliveryAcknowledge;
impl Operation for BoardContentBoardDeliveryAcknowledge { type Request = BoardContentAckRequest; type Response = BoardContentAckResult; type Error = BoardContentError; const NAME: &'static str = "board.delivery.acknowledge"; }

pub struct BoardContentBoardLinksCreate;
impl Operation for BoardContentBoardLinksCreate { type Request = BoardContentLinkCreateRequest; type Response = BoardContentLinkResult; type Error = BoardContentError; const NAME: &'static str = "board.links.create"; }

pub struct BoardContentBoardLinksRemove;
impl Operation for BoardContentBoardLinksRemove { type Request = BoardContentLinkRemoveRequest; type Response = BoardContentLinkResult; type Error = BoardContentError; const NAME: &'static str = "board.links.remove"; }

pub struct BoardContentBoardLinksList;
impl Operation for BoardContentBoardLinksList { type Request = BoardContentLinkListRequest; type Response = BoardContentLinkPage; type Error = BoardContentError; const NAME: &'static str = "board.links.list"; }

pub struct DevicesLocalInspect;
impl Operation for DevicesLocalInspect { type Request = ConnectivityLocalRequest; type Response = ConnectivityLocalView; type Error = ConnectivityError; const NAME: &'static str = "devices.local.inspect"; }

pub struct DevicesLocalEndpointsList;
impl Operation for DevicesLocalEndpointsList { type Request = ConnectivityLocalRequest; type Response = ConnectivityEndpointSnapshot; type Error = ConnectivityError; const NAME: &'static str = "devices.local.endpoints.list"; }

pub struct DevicesLocalMembershipsList;
impl Operation for DevicesLocalMembershipsList { type Request = ConnectivityLocalRequest; type Response = ConnectivityMembershipSnapshot; type Error = ConnectivityError; const NAME: &'static str = "devices.local.memberships.list"; }

pub struct DevicesLocalWatch;
impl Operation for DevicesLocalWatch { type Request = ConnectivityLocalRequest; type Response = ConnectivityLocalEvent; type Error = ConnectivityError; const NAME: &'static str = "devices.local.watch"; }

pub struct DevicesInspect;
impl Operation for DevicesInspect { type Request = ConnectivityDeviceInspectRequest; type Response = ConnectivityDeviceView; type Error = ConnectivityError; const NAME: &'static str = "devices.inspect"; }

pub struct DevicesList;
impl Operation for DevicesList { type Request = ConnectivityListRequest; type Response = ConnectivityDeviceSnapshot; type Error = ConnectivityError; const NAME: &'static str = "devices.list"; }

pub struct DevicesWatch;
impl Operation for DevicesWatch { type Request = ConnectivityListRequest; type Response = ConnectivityDeviceEvent; type Error = ConnectivityError; const NAME: &'static str = "devices.watch"; }

pub struct PoolsInspect;
impl Operation for PoolsInspect { type Request = ConnectivityPoolInspectRequest; type Response = ConnectivityPoolView; type Error = ConnectivityError; const NAME: &'static str = "pools.inspect"; }

pub struct PoolsList;
impl Operation for PoolsList { type Request = ConnectivityListRequest; type Response = ConnectivityPoolSnapshot; type Error = ConnectivityError; const NAME: &'static str = "pools.list"; }

pub struct PoolsWatch;
impl Operation for PoolsWatch { type Request = ConnectivityListRequest; type Response = ConnectivityPoolEvent; type Error = ConnectivityError; const NAME: &'static str = "pools.watch"; }

pub struct DiscoveryCandidateSubmitHint;
impl Operation for DiscoveryCandidateSubmitHint { type Request = ConnectivitySubmitHintRequest; type Response = ConnectivityCandidate; type Error = ConnectivityError; const NAME: &'static str = "discovery.candidate.submit.hint"; }

pub struct DiscoveryCandidateEndpointsList;
impl Operation for DiscoveryCandidateEndpointsList { type Request = ConnectivityCandidateEndpointsRequest; type Response = ConnectivityEndpointSnapshot; type Error = ConnectivityError; const NAME: &'static str = "discovery.candidate.endpoints.list"; }

pub struct DiscoveryCandidateInspect;
impl Operation for DiscoveryCandidateInspect { type Request = ConnectivityCandidateInspectRequest; type Response = ConnectivityCandidate; type Error = ConnectivityError; const NAME: &'static str = "discovery.candidate.inspect"; }

pub struct DiscoveryCandidatesList;
impl Operation for DiscoveryCandidatesList { type Request = ConnectivityListRequest; type Response = ConnectivityCandidateSnapshot; type Error = ConnectivityError; const NAME: &'static str = "discovery.candidates.list"; }

pub struct DiscoveryCandidatesWatch;
impl Operation for DiscoveryCandidatesWatch { type Request = ConnectivityListRequest; type Response = ConnectivityCandidateEvent; type Error = ConnectivityError; const NAME: &'static str = "discovery.candidates.watch"; }

pub struct PoolsJoinRequest;
impl Operation for PoolsJoinRequest { type Request = ConnectivityJoinRequest; type Response = ConnectivityJoinView; type Error = ConnectivityError; const NAME: &'static str = "pools.join.request"; }

pub struct PoolsJoinInspect;
impl Operation for PoolsJoinInspect { type Request = ConnectivityJoinInspectRequest; type Response = ConnectivityJoinView; type Error = ConnectivityError; const NAME: &'static str = "pools.join.inspect"; }

pub struct PoolsJoinCancel;
impl Operation for PoolsJoinCancel { type Request = ConnectivityJoinCancelRequest; type Response = ConnectivityJoinCancelResult; type Error = ConnectivityError; const NAME: &'static str = "pools.join.cancel"; }

pub struct PoolsJoinWatch;
impl Operation for PoolsJoinWatch { type Request = ConnectivityJoinWatchRequest; type Response = ConnectivityJoinEvent; type Error = ConnectivityError; const NAME: &'static str = "pools.join.watch"; }

pub struct TransportConnectionsList;
impl Operation for TransportConnectionsList { type Request = ConnectivityListRequest; type Response = ConnectivityConnectionSnapshot; type Error = ConnectivityError; const NAME: &'static str = "transport.connections.list"; }

pub struct TransportConnectionsWatch;
impl Operation for TransportConnectionsWatch { type Request = ConnectivityListRequest; type Response = ConnectivityConnectionEvent; type Error = ConnectivityError; const NAME: &'static str = "transport.connections.watch"; }

pub struct TransportConnectRequest;
impl Operation for TransportConnectRequest { type Request = ConnectivityConnectRequest; type Response = ConnectivityConnectionInterest; type Error = ConnectivityError; const NAME: &'static str = "transport.connect.request"; }

pub struct TransportDisconnect;
impl Operation for TransportDisconnect { type Request = ConnectivityDisconnectRequest; type Response = ConnectivityConnectionInterest; type Error = ConnectivityError; const NAME: &'static str = "transport.disconnect"; }

pub struct PoolsSyncRequest;
impl Operation for PoolsSyncRequest { type Request = ConnectivitySyncRequest; type Response = ConnectivitySyncView; type Error = ConnectivityError; const NAME: &'static str = "pools.sync.request"; }

pub struct PoolsSyncMembersList;
impl Operation for PoolsSyncMembersList { type Request = ConnectivitySyncMemberListRequest; type Response = ConnectivityMemberSyncSnapshot; type Error = ConnectivityError; const NAME: &'static str = "pools.sync.members.list"; }

pub struct PoolsSyncInspect;
impl Operation for PoolsSyncInspect { type Request = ConnectivitySyncInspectRequest; type Response = ConnectivitySyncView; type Error = ConnectivityError; const NAME: &'static str = "pools.sync.inspect"; }

pub struct PoolsSyncCancel;
impl Operation for PoolsSyncCancel { type Request = ConnectivitySyncCancelRequest; type Response = ConnectivitySyncCancelResult; type Error = ConnectivityError; const NAME: &'static str = "pools.sync.cancel"; }

pub struct PoolsSyncWatch;
impl Operation for PoolsSyncWatch { type Request = ConnectivitySyncWatchRequest; type Response = ConnectivitySyncEvent; type Error = ConnectivityError; const NAME: &'static str = "pools.sync.watch"; }

pub struct ConversationsDefaultsInspect;
impl Operation for ConversationsDefaultsInspect { type Request = ConversationDefaultsInspectRequest; type Response = ConversationDefaultsView; type Error = ConversationError; const NAME: &'static str = "conversations.defaults.inspect"; }

pub struct ConversationsList;
impl Operation for ConversationsList { type Request = ConversationListRequest; type Response = ConversationListEvent; type Error = ConversationError; const NAME: &'static str = "conversations.list"; }

pub struct ConversationsCreate;
impl Operation for ConversationsCreate { type Request = ConversationCreateRequest; type Response = ConversationCreationView; type Error = ConversationError; const NAME: &'static str = "conversations.create"; }

pub struct ConversationsCreationsInspect;
impl Operation for ConversationsCreationsInspect { type Request = ConversationCreationInspectRequest; type Response = ConversationCreationLookup; type Error = ConversationError; const NAME: &'static str = "conversations.creations.inspect"; }

pub struct ConversationsGet;
impl Operation for ConversationsGet { type Request = ConversationGetRequest; type Response = ConversationSummaryView; type Error = ConversationError; const NAME: &'static str = "conversations.get"; }

pub struct ConversationsMessagesList;
impl Operation for ConversationsMessagesList { type Request = ConversationMessagesListRequest; type Response = ConversationMessagesEvent; type Error = ConversationError; const NAME: &'static str = "conversations.messages.list"; }

pub struct ConversationsUpdate;
impl Operation for ConversationsUpdate { type Request = ConversationUpdateRequest; type Response = ConversationSummaryView; type Error = ConversationError; const NAME: &'static str = "conversations.update"; }

pub struct ConversationsRemove;
impl Operation for ConversationsRemove { type Request = ConversationRemoveRequest; type Response = ConversationRemovalView; type Error = ConversationError; const NAME: &'static str = "conversations.remove"; }

pub struct ConversationsContentBegin;
impl Operation for ConversationsContentBegin { type Request = ConversationContentBeginRequest; type Response = ConversationContentStageView; type Error = ConversationError; const NAME: &'static str = "conversations.content.begin"; }

pub struct ConversationsContentAppend;
impl Operation for ConversationsContentAppend { type Request = ConversationContentAppendRequest; type Response = ConversationContentStageView; type Error = ConversationError; const NAME: &'static str = "conversations.content.append"; }

pub struct ConversationsContentFinalize;
impl Operation for ConversationsContentFinalize { type Request = ConversationContentFinalizeRequest; type Response = ConversationContentFinalizeView; type Error = ConversationError; const NAME: &'static str = "conversations.content.finalize"; }

pub struct ConversationsContentRead;
impl Operation for ConversationsContentRead { type Request = ConversationContentReadRequest; type Response = InferenceContentChunk; type Error = ConversationError; const NAME: &'static str = "conversations.content.read"; }

pub struct ConversationsContentDiscard;
impl Operation for ConversationsContentDiscard { type Request = ConversationContentDiscardRequest; type Response = ConversationContentDiscardView; type Error = ConversationError; const NAME: &'static str = "conversations.content.discard"; }

pub struct CredentialList;
impl Operation for CredentialList { type Request = CredentialListRequest; type Response = CredentialListResponse; type Error = CredentialError; const NAME: &'static str = "credentials.list"; }

pub struct CredentialInspect;
impl Operation for CredentialInspect { type Request = CredentialInspectRequest; type Response = CredentialInspectView; type Error = CredentialError; const NAME: &'static str = "credentials.inspect"; }

pub struct CredentialSources;
impl Operation for CredentialSources { type Request = CredentialSourcesRequest; type Response = CredentialSourcesResponse; type Error = CredentialError; const NAME: &'static str = "credentials.sources"; }

pub struct CredentialChangeRequestCall;
impl Operation for CredentialChangeRequestCall { type Request = CredentialChangeRequest; type Response = CredentialChangeView; type Error = CredentialError; const NAME: &'static str = "credentials.change.request"; }

pub struct CredentialChangeInspect;
impl Operation for CredentialChangeInspect { type Request = CredentialChangeInspectRequest; type Response = CredentialChangeView; type Error = CredentialError; const NAME: &'static str = "credentials.change.inspect"; }

pub struct CredentialChangeWatch;
impl Operation for CredentialChangeWatch { type Request = CredentialChangeWatchRequest; type Response = CredentialChangeEvent; type Error = CredentialError; const NAME: &'static str = "credentials.change.watch"; }

pub struct DatasetDomainDatasetsCatalogSearch;
impl Operation for DatasetDomainDatasetsCatalogSearch { type Request = DatasetSearchRequest; type Response = DatasetSearchResult; type Error = DatasetError; const NAME: &'static str = "datasets.catalog.search"; }

pub struct DatasetDomainDatasetsCatalogInspect;
impl Operation for DatasetDomainDatasetsCatalogInspect { type Request = DatasetInspectRequest; type Response = DatasetDetails; type Error = DatasetError; const NAME: &'static str = "datasets.catalog.inspect"; }

pub struct DatasetDomainDatasetsCatalogSplitsList;
impl Operation for DatasetDomainDatasetsCatalogSplitsList { type Request = DatasetSplitListRequest; type Response = DatasetSplitPage; type Error = DatasetError; const NAME: &'static str = "datasets.catalog.splits.list"; }

pub struct DatasetDomainDatasetsCatalogPreview;
impl Operation for DatasetDomainDatasetsCatalogPreview { type Request = DatasetPreviewRequest; type Response = DatasetPreview; type Error = DatasetError; const NAME: &'static str = "datasets.catalog.preview"; }

pub struct DatasetDomainDatasetsCatalogSelectionCreate;
impl Operation for DatasetDomainDatasetsCatalogSelectionCreate { type Request = DatasetSelectionRequest; type Response = DatasetSelection; type Error = DatasetError; const NAME: &'static str = "datasets.catalog.selection.create"; }

pub struct DatasetDomainDatasetsCatalogSelectionFilesList;
impl Operation for DatasetDomainDatasetsCatalogSelectionFilesList { type Request = DatasetSelectionFilesRequest; type Response = DatasetFilePage; type Error = DatasetError; const NAME: &'static str = "datasets.catalog.selection.files.list"; }

pub struct DatasetDomainDatasetsLocalChoose;
impl Operation for DatasetDomainDatasetsLocalChoose { type Request = DatasetLocalChooseRequest; type Response = DatasetChoice; type Error = DatasetError; const NAME: &'static str = "datasets.local.choose"; }

pub struct DatasetDomainDatasetsLocalChoiceInspect;
impl Operation for DatasetDomainDatasetsLocalChoiceInspect { type Request = DatasetChoiceInspectRequest; type Response = DatasetChoice; type Error = DatasetError; const NAME: &'static str = "datasets.local.choice.inspect"; }

pub struct DatasetDomainDatasetsLocalChoiceCancel;
impl Operation for DatasetDomainDatasetsLocalChoiceCancel { type Request = DatasetChoiceCancelRequest; type Response = DatasetChoice; type Error = DatasetError; const NAME: &'static str = "datasets.local.choice.cancel"; }

pub struct DatasetDomainDatasetsLocalPreview;
impl Operation for DatasetDomainDatasetsLocalPreview { type Request = DatasetLocalPreviewRequest; type Response = DatasetPreview; type Error = DatasetError; const NAME: &'static str = "datasets.local.preview"; }

pub struct DatasetDomainDatasetsManagedList;
impl Operation for DatasetDomainDatasetsManagedList { type Request = DatasetManagedListRequest; type Response = DatasetManagedPage; type Error = DatasetError; const NAME: &'static str = "datasets.managed.list"; }

pub struct DatasetDomainDatasetsManagedGet;
impl Operation for DatasetDomainDatasetsManagedGet { type Request = DatasetManagedGetRequest; type Response = DatasetView; type Error = DatasetError; const NAME: &'static str = "datasets.managed.get"; }

pub struct DatasetDomainDatasetsImportPreviewStart;
impl Operation for DatasetDomainDatasetsImportPreviewStart { type Request = DatasetImportStartRequest; type Response = DatasetImportView; type Error = DatasetError; const NAME: &'static str = "datasets.import.preview.start"; }

pub struct DatasetDomainDatasetsImportLookup;
impl Operation for DatasetDomainDatasetsImportLookup { type Request = DatasetImportLookupRequest; type Response = DatasetImportView; type Error = DatasetError; const NAME: &'static str = "datasets.import.lookup"; }

pub struct DatasetDomainDatasetsImportGet;
impl Operation for DatasetDomainDatasetsImportGet { type Request = DatasetImportGetRequest; type Response = DatasetImportView; type Error = DatasetError; const NAME: &'static str = "datasets.import.get"; }

pub struct DatasetDomainDatasetsImportCancel;
impl Operation for DatasetDomainDatasetsImportCancel { type Request = DatasetImportCancelRequest; type Response = DatasetImportCancelResult; type Error = DatasetError; const NAME: &'static str = "datasets.import.cancel"; }

pub struct DatasetDomainDatasetsImportWatch;
impl Operation for DatasetDomainDatasetsImportWatch { type Request = DatasetImportWatchRequest; type Response = DatasetImportWatchResult; type Error = DatasetError; const NAME: &'static str = "datasets.import.watch"; }

pub struct DatasetDomainDatasetsValuesRead;
impl Operation for DatasetDomainDatasetsValuesRead { type Request = DatasetValueReadRequest; type Response = DatasetValueView; type Error = DatasetError; const NAME: &'static str = "datasets.values.read"; }

pub struct DatasetDomainDatasetsTextRead;
impl Operation for DatasetDomainDatasetsTextRead { type Request = DatasetTextReadRequest; type Response = DatasetTextChunk; type Error = DatasetError; const NAME: &'static str = "datasets.text.read"; }

pub struct InferenceDomainInferenceChatStart;
impl Operation for InferenceDomainInferenceChatStart { type Request = InferenceChatStartRequest; type Response = InferenceGenerationView; type Error = InferenceError; const NAME: &'static str = "inference.chat.start"; }

pub struct InferenceDomainInferenceResponsesStart;
impl Operation for InferenceDomainInferenceResponsesStart { type Request = InferenceResponsesStartRequest; type Response = InferenceGenerationView; type Error = InferenceError; const NAME: &'static str = "inference.responses.start"; }

pub struct InferenceDomainInferenceMessagesStart;
impl Operation for InferenceDomainInferenceMessagesStart { type Request = InferenceMessagesStartRequest; type Response = InferenceGenerationView; type Error = InferenceError; const NAME: &'static str = "inference.messages.start"; }

pub struct InferenceDomainInferenceCompletionStart;
impl Operation for InferenceDomainInferenceCompletionStart { type Request = InferenceCompletionStartRequest; type Response = InferenceGenerationView; type Error = InferenceError; const NAME: &'static str = "inference.completion.start"; }

pub struct InferenceDomainInferenceEmbeddingsStart;
impl Operation for InferenceDomainInferenceEmbeddingsStart { type Request = InferenceEmbeddingStartRequest; type Response = InferenceGenerationView; type Error = InferenceError; const NAME: &'static str = "inference.embeddings.start"; }

pub struct InferenceDomainInferenceRerankStart;
impl Operation for InferenceDomainInferenceRerankStart { type Request = InferenceRerankStartRequest; type Response = InferenceGenerationView; type Error = InferenceError; const NAME: &'static str = "inference.rerank.start"; }

pub struct InferenceDomainInferenceTokenize;
impl Operation for InferenceDomainInferenceTokenize { type Request = InferenceTokenizeRequest; type Response = InferenceTokenizeResult; type Error = InferenceError; const NAME: &'static str = "inference.tokenize"; }

pub struct InferenceDomainInferenceDetokenize;
impl Operation for InferenceDomainInferenceDetokenize { type Request = InferenceDetokenizeRequest; type Response = InferenceDetokenizeResult; type Error = InferenceError; const NAME: &'static str = "inference.detokenize"; }

pub struct InferenceDomainInferenceCountTokens;
impl Operation for InferenceDomainInferenceCountTokens { type Request = InferenceCountRequest; type Response = InferenceCountResult; type Error = InferenceError; const NAME: &'static str = "inference.count.tokens"; }

pub struct InferenceDomainInferenceGet;
impl Operation for InferenceDomainInferenceGet { type Request = InferenceGetRequest; type Response = InferenceGenerationView; type Error = InferenceError; const NAME: &'static str = "inference.get"; }

pub struct InferenceDomainInferenceCancel;
impl Operation for InferenceDomainInferenceCancel { type Request = InferenceCancelRequest; type Response = InferenceCancelResult; type Error = InferenceError; const NAME: &'static str = "inference.cancel"; }

pub struct InferenceDomainInferenceWatch;
impl Operation for InferenceDomainInferenceWatch { type Request = InferenceWatchRequest; type Response = InferenceWatchBatch; type Error = InferenceError; const NAME: &'static str = "inference.watch"; }

pub struct InferenceDomainChatTurnSend;
impl Operation for InferenceDomainChatTurnSend { type Request = InferenceTurnSendRequest; type Response = InferenceTurnView; type Error = InferenceError; const NAME: &'static str = "chat.turn.send"; }

pub struct InferenceDomainChatTurnGet;
impl Operation for InferenceDomainChatTurnGet { type Request = InferenceTurnGetRequest; type Response = InferenceTurnView; type Error = InferenceError; const NAME: &'static str = "chat.turn.get"; }

pub struct InferenceDomainChatTurnCancel;
impl Operation for InferenceDomainChatTurnCancel { type Request = InferenceTurnCancelRequest; type Response = InferenceTurnCancelResult; type Error = InferenceError; const NAME: &'static str = "chat.turn.cancel"; }

pub struct InferenceDomainChatTurnWatch;
impl Operation for InferenceDomainChatTurnWatch { type Request = InferenceTurnWatchRequest; type Response = InferenceTurnWatchBatch; type Error = InferenceError; const NAME: &'static str = "chat.turn.watch"; }

pub struct InferenceDomainInferenceToolsRespond;
impl Operation for InferenceDomainInferenceToolsRespond { type Request = InferenceToolResultSubmitRequest; type Response = InferenceToolResultAcceptance; type Error = InferenceError; const NAME: &'static str = "inference.tools.respond"; }

pub struct InferenceDomainInferenceContentBegin;
impl Operation for InferenceDomainInferenceContentBegin { type Request = InferenceContentBeginRequest; type Response = InferenceStageView; type Error = InferenceError; const NAME: &'static str = "inference.content.begin"; }

pub struct InferenceDomainInferenceContentAppend;
impl Operation for InferenceDomainInferenceContentAppend { type Request = InferenceContentAppendRequest; type Response = InferenceStageView; type Error = InferenceError; const NAME: &'static str = "inference.content.append"; }

pub struct InferenceDomainInferenceContentFinalize;
impl Operation for InferenceDomainInferenceContentFinalize { type Request = InferenceContentFinalizeRequest; type Response = InferenceContentFinalizeResult; type Error = InferenceError; const NAME: &'static str = "inference.content.finalize"; }

pub struct InferenceDomainInferenceContentRead;
impl Operation for InferenceDomainInferenceContentRead { type Request = InferenceContentReadRequest; type Response = InferenceContentChunk; type Error = InferenceError; const NAME: &'static str = "inference.content.read"; }

pub struct InferenceDomainInferenceContentDiscard;
impl Operation for InferenceDomainInferenceContentDiscard { type Request = InferenceContentDiscardRequest; type Response = InferenceContentDiscardResult; type Error = InferenceError; const NAME: &'static str = "inference.content.discard"; }

pub struct InferenceDomainInferencePrepare;
impl Operation for InferenceDomainInferencePrepare { type Request = InferencePrepareRequest; type Response = InferencePreparationView; type Error = InferenceError; const NAME: &'static str = "inference.prepare"; }

pub struct JobsPrepare;
impl Operation for JobsPrepare { type Request = JobPrepareRequest; type Response = JobPreparationView; type Error = JobError; const NAME: &'static str = "jobs.prepare"; }

pub struct JobsSubmit;
impl Operation for JobsSubmit { type Request = JobPreparationControlRequest; type Response = JobPreparationView; type Error = JobError; const NAME: &'static str = "jobs.submit"; }

pub struct JobsPreparationsInspect;
impl Operation for JobsPreparationsInspect { type Request = JobPreparationInspectRequest; type Response = JobPreparationView; type Error = JobError; const NAME: &'static str = "jobs.preparations.inspect"; }

pub struct JobsPreparationsCancel;
impl Operation for JobsPreparationsCancel { type Request = JobPreparationControlRequest; type Response = JobPreparationView; type Error = JobError; const NAME: &'static str = "jobs.preparations.cancel"; }

pub struct JobsPreparationsWatch;
impl Operation for JobsPreparationsWatch { type Request = JobPreparationWatchRequest; type Response = JobPreparationEvent; type Error = JobError; const NAME: &'static str = "jobs.preparations.watch"; }

pub struct KnowledgeContextInspect;
impl Operation for KnowledgeContextInspect { type Request = KnowledgeContextInspectRequest; type Response = KnowledgeContextView; type Error = KnowledgeError; const NAME: &'static str = "knowledge.context.inspect"; }

pub struct KnowledgeStoresList;
impl Operation for KnowledgeStoresList { type Request = KnowledgeStoresListRequest; type Response = KnowledgeStoresEvent; type Error = KnowledgeError; const NAME: &'static str = "knowledge.stores.list"; }

pub struct KnowledgeNodesSearch;
impl Operation for KnowledgeNodesSearch { type Request = KnowledgeNodesSearchRequest; type Response = KnowledgeNodesEvent; type Error = KnowledgeError; const NAME: &'static str = "knowledge.nodes.search"; }

pub struct KnowledgeNodeInspect;
impl Operation for KnowledgeNodeInspect { type Request = KnowledgeNodeInspectRequest; type Response = KnowledgeNodeDetailEvent; type Error = KnowledgeError; const NAME: &'static str = "knowledge.node.inspect"; }

pub struct KnowledgeFieldsRead;
impl Operation for KnowledgeFieldsRead { type Request = KnowledgeFieldsReadRequest; type Response = KnowledgeFieldEvent; type Error = KnowledgeError; const NAME: &'static str = "knowledge.fields.read"; }

pub struct KnowledgeTextRead;
impl Operation for KnowledgeTextRead { type Request = KnowledgeTextReadRequest; type Response = KnowledgeDiagnosticTextEvent; type Error = KnowledgeError; const NAME: &'static str = "knowledge.text.read"; }

pub struct ModelsDomainModelsList;
impl Operation for ModelsDomainModelsList { type Request = ModelListRequest; type Response = ModelListResponse; type Error = ModelDomainError; const NAME: &'static str = "models.list"; }

pub struct ModelsDomainModelsGet;
impl Operation for ModelsDomainModelsGet { type Request = ModelGetRequest; type Response = ModelView; type Error = ModelDomainError; const NAME: &'static str = "models.get"; }

pub struct ModelsDomainModelsSearch;
impl Operation for ModelsDomainModelsSearch { type Request = ModelSearchRequest; type Response = ModelSearchResponse; type Error = ModelDomainError; const NAME: &'static str = "models.search"; }

pub struct ModelsDomainModelsRecommend;
impl Operation for ModelsDomainModelsRecommend { type Request = ModelRecommendRequest; type Response = ModelRecommendResponse; type Error = ModelDomainError; const NAME: &'static str = "models.recommend"; }

pub struct ModelsDomainModelsLocate;
impl Operation for ModelsDomainModelsLocate { type Request = ModelLocateRequest; type Response = ModelLocateResponse; type Error = ModelDomainError; const NAME: &'static str = "models.locate"; }

pub struct ModelsDomainModelsComponentsList;
impl Operation for ModelsDomainModelsComponentsList { type Request = ModelComponentsRequest; type Response = ModelComponentsResponse; type Error = ModelDomainError; const NAME: &'static str = "models.components.list"; }

pub struct ModelsDomainModelsFit;
impl Operation for ModelsDomainModelsFit { type Request = ModelFitRequest; type Response = ModelFitAssessment; type Error = ModelDomainError; const NAME: &'static str = "models.fit"; }

pub struct ModelsDomainResourcesModelsGet;
impl Operation for ModelsDomainResourcesModelsGet { type Request = ModelResourceRequest; type Response = ModelResourceSnapshot; type Error = ModelDomainError; const NAME: &'static str = "resources.models.get"; }

pub struct ModelsDomainAssetsDownloadPlan;
impl Operation for ModelsDomainAssetsDownloadPlan { type Request = ModelDownloadPlanRequest; type Response = ModelDownloadPlan; type Error = ModelDomainError; const NAME: &'static str = "assets.download.plan"; }

pub struct ModelsDomainAssetsDownloadStart;
impl Operation for ModelsDomainAssetsDownloadStart { type Request = ModelTransferStartRequest; type Response = ModelTransferView; type Error = ModelDomainError; const NAME: &'static str = "assets.download.start"; }

pub struct ModelsDomainAssetsDownloadGet;
impl Operation for ModelsDomainAssetsDownloadGet { type Request = ModelTransferGetRequest; type Response = ModelTransferView; type Error = ModelDomainError; const NAME: &'static str = "assets.download.get"; }

pub struct ModelsDomainAssetsDownloadResume;
impl Operation for ModelsDomainAssetsDownloadResume { type Request = ModelTransferControlRequest; type Response = ModelTransferView; type Error = ModelDomainError; const NAME: &'static str = "assets.download.resume"; }

pub struct ModelsDomainAssetsDownloadCancel;
impl Operation for ModelsDomainAssetsDownloadCancel { type Request = ModelTransferControlRequest; type Response = ModelTransferCancelResult; type Error = ModelDomainError; const NAME: &'static str = "assets.download.cancel"; }

pub struct ModelsDomainAssetsList;
impl Operation for ModelsDomainAssetsList { type Request = ModelAssetListRequest; type Response = ModelAssetListResponse; type Error = ModelDomainError; const NAME: &'static str = "assets.list"; }

pub struct ModelsDomainAssetsGet;
impl Operation for ModelsDomainAssetsGet { type Request = ModelAssetGetRequest; type Response = ModelAssetView; type Error = ModelDomainError; const NAME: &'static str = "assets.get"; }

pub struct ModelsDomainAssetsRemove;
impl Operation for ModelsDomainAssetsRemove { type Request = ModelAssetRemoveRequest; type Response = ModelAssetRemoveResult; type Error = ModelDomainError; const NAME: &'static str = "assets.remove"; }

pub struct ModelsDomainAssetsPartialDiscard;
impl Operation for ModelsDomainAssetsPartialDiscard { type Request = ModelPartialDiscardRequest; type Response = ModelPartialDiscardResult; type Error = ModelDomainError; const NAME: &'static str = "assets.partial.discard"; }

pub struct ModelsDomainAssetsImportPlan;
impl Operation for ModelsDomainAssetsImportPlan { type Request = ModelImportPlanRequest; type Response = ModelImportPlan; type Error = ModelDomainError; const NAME: &'static str = "assets.import.plan"; }

pub struct ModelsDomainAssetsImportCommit;
impl Operation for ModelsDomainAssetsImportCommit { type Request = ModelImportCommitRequest; type Response = ModelImportCommitResult; type Error = ModelDomainError; const NAME: &'static str = "assets.import.commit"; }

pub struct ModelsDomainServingList;
impl Operation for ModelsDomainServingList { type Request = ModelServingListRequest; type Response = ModelServingListResponse; type Error = ModelDomainError; const NAME: &'static str = "serving.list"; }

pub struct ModelsDomainServingGet;
impl Operation for ModelsDomainServingGet { type Request = ModelServingGetRequest; type Response = ModelServingView; type Error = ModelDomainError; const NAME: &'static str = "serving.get"; }

pub struct ModelsDomainServingAcquire;
impl Operation for ModelsDomainServingAcquire { type Request = ModelServingAcquireRequest; type Response = ModelServingAcquireResult; type Error = ModelDomainError; const NAME: &'static str = "serving.acquire"; }

pub struct ModelsDomainServingRelease;
impl Operation for ModelsDomainServingRelease { type Request = ModelServingReleaseRequest; type Response = ModelServingReleaseResult; type Error = ModelDomainError; const NAME: &'static str = "serving.release"; }

pub struct ModelsDomainServingRenew;
impl Operation for ModelsDomainServingRenew { type Request = ModelServingRenewRequest; type Response = ModelServingRenewResult; type Error = ModelDomainError; const NAME: &'static str = "serving.renew"; }

pub struct ModelsDomainServingStop;
impl Operation for ModelsDomainServingStop { type Request = ModelServingStopRequest; type Response = ModelServingStopResult; type Error = ModelDomainError; const NAME: &'static str = "serving.stop"; }

pub struct ModelsDomainModelsWatch;
impl Operation for ModelsDomainModelsWatch { type Request = ModelWatchRequest; type Response = ModelWatchBatch; type Error = ModelDomainError; const NAME: &'static str = "models.watch"; }

pub struct ModelsDomainAssetsDownloadWatch;
impl Operation for ModelsDomainAssetsDownloadWatch { type Request = ModelWatchRequest; type Response = ModelWatchBatch; type Error = ModelDomainError; const NAME: &'static str = "assets.download.watch"; }

pub struct ModelsDomainServingWatch;
impl Operation for ModelsDomainServingWatch { type Request = ModelWatchRequest; type Response = ModelWatchBatch; type Error = ModelDomainError; const NAME: &'static str = "serving.watch"; }

pub struct ModelsDomainResourcesModelsWatch;
impl Operation for ModelsDomainResourcesModelsWatch { type Request = ModelWatchRequest; type Response = ModelWatchBatch; type Error = ModelDomainError; const NAME: &'static str = "resources.models.watch"; }

pub struct ModelsDomainModelsStartupChoices;
impl Operation for ModelsDomainModelsStartupChoices { type Request = ModelStartupChoicesRequest; type Response = ModelStartupChoicesResponse; type Error = ModelDomainError; const NAME: &'static str = "models.startup.choices"; }

pub struct ProjectsCatalogInspect;
impl Operation for ProjectsCatalogInspect { type Request = Empty; type Response = ProjectRegistryContext; type Error = ProjectCatalogError; const NAME: &'static str = "projects.catalog.inspect"; }

pub struct ProjectsLocationsImport;
impl Operation for ProjectsLocationsImport { type Request = ProjectLocationImportRequest; type Response = ProjectLocationView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.locations.import"; }

pub struct ProjectsLocationsChoose;
impl Operation for ProjectsLocationsChoose { type Request = ProjectLocationChooseRequest; type Response = ProjectLocationView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.locations.choose"; }

pub struct ProjectsLocationsInspect;
impl Operation for ProjectsLocationsInspect { type Request = ProjectLocationInspectRequest; type Response = ProjectLocationView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.locations.inspect"; }

pub struct ProjectsLocationsCancel;
impl Operation for ProjectsLocationsCancel { type Request = ProjectLocationCancelRequest; type Response = ProjectLocationCancelResult; type Error = ProjectCatalogError; const NAME: &'static str = "projects.locations.cancel"; }

pub struct ProjectsLocationsWatch;
impl Operation for ProjectsLocationsWatch { type Request = ProjectLocationWatchRequest; type Response = ProjectLocationEvent; type Error = ProjectCatalogError; const NAME: &'static str = "projects.locations.watch"; }

pub struct ProjectsAdd;
impl Operation for ProjectsAdd { type Request = ProjectAddRequest; type Response = ProjectCatalogView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.add"; }

pub struct ProjectsInspect;
impl Operation for ProjectsInspect { type Request = ProjectInspectRequest; type Response = ProjectCatalogView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.inspect"; }

pub struct ProjectsList;
impl Operation for ProjectsList { type Request = ProjectListRequest; type Response = ProjectCatalogSnapshot; type Error = ProjectCatalogError; const NAME: &'static str = "projects.list"; }

pub struct ProjectsWatch;
impl Operation for ProjectsWatch { type Request = ProjectListRequest; type Response = ProjectCatalogEvent; type Error = ProjectCatalogError; const NAME: &'static str = "projects.watch"; }

pub struct ProjectsResolve;
impl Operation for ProjectsResolve { type Request = ProjectResolveRequest; type Response = ProjectCatalogView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.resolve"; }

pub struct ProjectsSourcesAdd;
impl Operation for ProjectsSourcesAdd { type Request = ProjectSourceAddRequest; type Response = ProjectSourceView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.sources.add"; }

pub struct ProjectsSourcesList;
impl Operation for ProjectsSourcesList { type Request = ProjectSourcesRequest; type Response = ProjectSourceSnapshot; type Error = ProjectCatalogError; const NAME: &'static str = "projects.sources.list"; }

pub struct ProjectsCreateRequest;
impl Operation for ProjectsCreateRequest { type Request = ProjectCreateRequest; type Response = ProjectCreationView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.create.request"; }

pub struct ProjectsDefaultRequest;
impl Operation for ProjectsDefaultRequest { type Request = ProjectDefaultRequest; type Response = ProjectCreationView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.default.request"; }

pub struct ProjectsCreationInspect;
impl Operation for ProjectsCreationInspect { type Request = ProjectCreationInspectRequest; type Response = ProjectCreationView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.creation.inspect"; }

pub struct ProjectsCreationCancel;
impl Operation for ProjectsCreationCancel { type Request = ProjectCreationCancelRequest; type Response = ProjectCreationCancelResult; type Error = ProjectCatalogError; const NAME: &'static str = "projects.creation.cancel"; }

pub struct ProjectsCreationWatch;
impl Operation for ProjectsCreationWatch { type Request = ProjectCreationWatchRequest; type Response = ProjectCreationEvent; type Error = ProjectCatalogError; const NAME: &'static str = "projects.creation.watch"; }

pub struct ProjectsSelectionInspect;
impl Operation for ProjectsSelectionInspect { type Request = Empty; type Response = ProjectSelectionView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.selection.inspect"; }

pub struct ProjectsSelectionReplace;
impl Operation for ProjectsSelectionReplace { type Request = ProjectSelectionReplaceRequest; type Response = ProjectSelectionView; type Error = ProjectCatalogError; const NAME: &'static str = "projects.selection.replace"; }

pub struct ProjectsSelectionWatch;
impl Operation for ProjectsSelectionWatch { type Request = ProjectSelectionWatchRequest; type Response = ProjectSelectionEvent; type Error = ProjectCatalogError; const NAME: &'static str = "projects.selection.watch"; }

pub struct BoardsList;
impl Operation for BoardsList { type Request = ProjectListRequest; type Response = CatalogBoardSnapshot; type Error = ProjectCatalogError; const NAME: &'static str = "boards.list"; }

pub struct BoardsInspect;
impl Operation for BoardsInspect { type Request = CatalogBoardInspectRequest; type Response = CatalogBoardView; type Error = ProjectCatalogError; const NAME: &'static str = "boards.inspect"; }

pub struct BoardsResolve;
impl Operation for BoardsResolve { type Request = CatalogBoardResolveRequest; type Response = CatalogBoardView; type Error = ProjectCatalogError; const NAME: &'static str = "boards.resolve"; }

pub struct BoardsOpen;
impl Operation for BoardsOpen { type Request = CatalogBoardInspectRequest; type Response = CatalogBoardView; type Error = ProjectCatalogError; const NAME: &'static str = "boards.open"; }

pub struct ResourceLeasesContextInspect;
impl Operation for ResourceLeasesContextInspect { type Request = ResourceLeaseContextInspectRequest; type Response = ResourceLeaseContextView; type Error = ResourceLeaseError; const NAME: &'static str = "leases.context.inspect"; }

pub struct ResourceLeasesResourcesList;
impl Operation for ResourceLeasesResourcesList { type Request = ResourceLeaseResourcesListRequest; type Response = ResourceLeaseResourcesEvent; type Error = ResourceLeaseError; const NAME: &'static str = "leases.resources.list"; }

pub struct ResourceLeasesAcquire;
impl Operation for ResourceLeasesAcquire { type Request = ResourceLeaseAcquireRequest; type Response = ResourceLeaseAcquireResult; type Error = ResourceLeaseError; const NAME: &'static str = "leases.acquire"; }

pub struct ResourceLeasesGet;
impl Operation for ResourceLeasesGet { type Request = ResourceLeaseGetRequest; type Response = ResourceLeaseView; type Error = ResourceLeaseError; const NAME: &'static str = "leases.get"; }

pub struct ResourceLeasesRenew;
impl Operation for ResourceLeasesRenew { type Request = ResourceLeaseRenewRequest; type Response = ResourceLeaseView; type Error = ResourceLeaseError; const NAME: &'static str = "leases.renew"; }

pub struct ResourceLeasesRelease;
impl Operation for ResourceLeasesRelease { type Request = ResourceLeaseReleaseRequest; type Response = ResourceLeaseReleaseView; type Error = ResourceLeaseError; const NAME: &'static str = "leases.release"; }

pub struct ResourceLeasesWait;
impl Operation for ResourceLeasesWait { type Request = ResourceLeaseWaitRequest; type Response = ResourceLeaseWaitView; type Error = ResourceLeaseError; const NAME: &'static str = "leases.wait"; }

pub struct ResourceLeasesList;
impl Operation for ResourceLeasesList { type Request = ResourceLeaseListRequest; type Response = ResourceLeaseEvent; type Error = ResourceLeaseError; const NAME: &'static str = "leases.list"; }

pub struct ResourceLeasesWatch;
impl Operation for ResourceLeasesWatch { type Request = ResourceLeaseWatchRequest; type Response = ResourceLeaseEvent; type Error = ResourceLeaseError; const NAME: &'static str = "leases.watch"; }

pub struct SettingsLocalDefaultsInspect;
impl Operation for SettingsLocalDefaultsInspect { type Request = SettingsReadRequest; type Response = SettingsLocalDefaultsView; type Error = SettingsError; const NAME: &'static str = "settings.local.inspect"; }

pub struct SettingsLocalDefaultsReplace;
impl Operation for SettingsLocalDefaultsReplace { type Request = SettingsLocalDefaultsReplaceRequest; type Response = SettingsLocalDefaultsView; type Error = SettingsError; const NAME: &'static str = "settings.local.replace"; }

pub struct SettingsExecutionPolicyInspect;
impl Operation for SettingsExecutionPolicyInspect { type Request = SettingsReadRequest; type Response = SettingsExecutionPolicyView; type Error = SettingsError; const NAME: &'static str = "settings.execution.inspect"; }

pub struct SettingsExecutionPolicyReplace;
impl Operation for SettingsExecutionPolicyReplace { type Request = SettingsExecutionPolicyReplaceRequest; type Response = SettingsExecutionPolicyView; type Error = SettingsError; const NAME: &'static str = "settings.execution.replace"; }

pub struct SettingsDownloadPolicyInspect;
impl Operation for SettingsDownloadPolicyInspect { type Request = SettingsReadRequest; type Response = SettingsDownloadPolicyView; type Error = SettingsError; const NAME: &'static str = "settings.downloads.inspect"; }

pub struct SettingsDownloadPolicyReplace;
impl Operation for SettingsDownloadPolicyReplace { type Request = SettingsDownloadPolicyReplaceRequest; type Response = SettingsDownloadPolicyView; type Error = SettingsError; const NAME: &'static str = "settings.downloads.replace"; }

pub struct SettingsUpdatePolicyInspect;
impl Operation for SettingsUpdatePolicyInspect { type Request = SettingsReadRequest; type Response = SettingsUpdatePolicyView; type Error = SettingsError; const NAME: &'static str = "settings.updates.inspect"; }

pub struct SettingsUpdatePolicyReplace;
impl Operation for SettingsUpdatePolicyReplace { type Request = SettingsUpdatePolicyReplaceRequest; type Response = SettingsUpdatePolicyView; type Error = SettingsError; const NAME: &'static str = "settings.updates.replace"; }

pub struct SettingsInterpreterPolicyInspect;
impl Operation for SettingsInterpreterPolicyInspect { type Request = SettingsReadRequest; type Response = SettingsInterpreterPolicyView; type Error = SettingsError; const NAME: &'static str = "settings.interpreters.inspect"; }

pub struct SettingsInterpreterPolicyReplace;
impl Operation for SettingsInterpreterPolicyReplace { type Request = SettingsInterpreterPolicyReplaceRequest; type Response = SettingsInterpreterPolicyView; type Error = SettingsError; const NAME: &'static str = "settings.interpreters.replace"; }

pub struct SettingsFeaturesInspect;
impl Operation for SettingsFeaturesInspect { type Request = SettingsReadRequest; type Response = SettingsFeaturesView; type Error = SettingsError; const NAME: &'static str = "settings.features.inspect"; }

pub struct SettingsFeaturesReplace;
impl Operation for SettingsFeaturesReplace { type Request = SettingsFeaturesReplaceRequest; type Response = SettingsFeaturesView; type Error = SettingsError; const NAME: &'static str = "settings.features.replace"; }

pub struct SettingsAvailabilityInspect;
impl Operation for SettingsAvailabilityInspect { type Request = SettingsReadRequest; type Response = SettingsAvailabilityView; type Error = SettingsError; const NAME: &'static str = "settings.availability.inspect"; }

pub struct SettingsAvailabilityReplace;
impl Operation for SettingsAvailabilityReplace { type Request = SettingsAvailabilityReplaceRequest; type Response = SettingsAvailabilityView; type Error = SettingsError; const NAME: &'static str = "settings.availability.replace"; }

pub struct SettingsWatch;
impl Operation for SettingsWatch { type Request = SettingsWatchRequest; type Response = SettingsEvent; type Error = SettingsError; const NAME: &'static str = "settings.watch"; }

pub struct SettingsAppearanceBootstrap;
impl Operation for SettingsAppearanceBootstrap { type Request = SettingsReadRequest; type Response = SettingsResolvedAppearance; type Error = SettingsError; const NAME: &'static str = "settings.appearance.bootstrap"; }

pub struct StartupInspect;
impl Operation for StartupInspect { type Request = SettingsReadRequest; type Response = StartupView; type Error = SettingsError; const NAME: &'static str = "startup.inspect"; }

pub struct StartupChangeStart;
impl Operation for StartupChangeStart { type Request = StartupChangeRequest; type Response = StartupChangeView; type Error = SettingsError; const NAME: &'static str = "startup.change.request"; }

pub struct StartupChangeInspect;
impl Operation for StartupChangeInspect { type Request = StartupChangeGetRequest; type Response = StartupChangeView; type Error = SettingsError; const NAME: &'static str = "startup.change.inspect"; }

pub struct StartupChangeCancel;
impl Operation for StartupChangeCancel { type Request = StartupChangeCancelRequest; type Response = StartupChangeView; type Error = SettingsError; const NAME: &'static str = "startup.change.cancel"; }

pub struct StartupWatch;
impl Operation for StartupWatch { type Request = SettingsWatchRequest; type Response = StartupEvent; type Error = SettingsError; const NAME: &'static str = "startup.watch"; }

pub struct SetupInspect;
impl Operation for SetupInspect { type Request = SettingsReadRequest; type Response = SetupView; type Error = SettingsError; const NAME: &'static str = "setup.inspect"; }

pub struct SetupNoticeAcknowledge;
impl Operation for SetupNoticeAcknowledge { type Request = SetupMutationRequest; type Response = SetupView; type Error = SettingsError; const NAME: &'static str = "setup.notice.acknowledge"; }

pub struct SetupSectionReview;
impl Operation for SetupSectionReview { type Request = SetupSectionReviewRequest; type Response = SetupView; type Error = SettingsError; const NAME: &'static str = "setup.section.review"; }

pub struct SetupComplete;
impl Operation for SetupComplete { type Request = SetupMutationRequest; type Response = SetupView; type Error = SettingsError; const NAME: &'static str = "setup.complete"; }

pub struct SettingsWindowCloseInspect;
impl Operation for SettingsWindowCloseInspect { type Request = SettingsWindowCloseRequest; type Response = SettingsWindowCloseView; type Error = SettingsError; const NAME: &'static str = "window.close.inspect"; }

pub struct SettingsWindowCloseChoose;
impl Operation for SettingsWindowCloseChoose { type Request = SettingsWindowChooseRequest; type Response = SettingsWindowCloseView; type Error = SettingsError; const NAME: &'static str = "window.close.choose"; }

pub struct StudioControlCatalogList;
impl Operation for StudioControlCatalogList { type Request = StudioControlListRequest; type Response = StudioControlReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.catalog.list"; }

pub struct StudioControlTemplatesList;
impl Operation for StudioControlTemplatesList { type Request = StudioControlListRequest; type Response = StudioControlReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.templates.list"; }

pub struct StudioControlAvailabilityInspect;
impl Operation for StudioControlAvailabilityInspect { type Request = StudioControlInspectRequest; type Response = StudioControlAvailabilityView; type Error = StudioControlError; const NAME: &'static str = "studio.availability.inspect"; }

pub struct StudioControlGraphStageBegin;
impl Operation for StudioControlGraphStageBegin { type Request = StudioControlGraphStageBeginRequest; type Response = StudioControlUploadView; type Error = StudioControlError; const NAME: &'static str = "studio.graphs.stage.begin"; }

pub struct StudioControlTitleStageBegin;
impl Operation for StudioControlTitleStageBegin { type Request = StudioControlTitleStageBeginRequest; type Response = StudioControlUploadView; type Error = StudioControlError; const NAME: &'static str = "studio.titles.stage.begin"; }

pub struct StudioControlStageWrite;
impl Operation for StudioControlStageWrite { type Request = StudioControlStageWriteRequest; type Response = StudioControlUploadView; type Error = StudioControlError; const NAME: &'static str = "studio.documents.stage.write"; }

pub struct StudioControlStageSeal;
impl Operation for StudioControlStageSeal { type Request = StudioControlStageSealRequest; type Response = StudioControlUploadView; type Error = StudioControlError; const NAME: &'static str = "studio.documents.stage.seal"; }

pub struct StudioControlDocumentRead;
impl Operation for StudioControlDocumentRead { type Request = StudioControlDocumentReadRequest; type Response = StudioControlDocumentChunk; type Error = StudioControlError; const NAME: &'static str = "studio.documents.read"; }

pub struct StudioControlGraphNodesList;
impl Operation for StudioControlGraphNodesList { type Request = StudioControlGraphNodeListRequest; type Response = StudioControlReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.graphs.nodes.list"; }

pub struct StudioControlGraphValidate;
impl Operation for StudioControlGraphValidate { type Request = StudioControlValidateRequest; type Response = StudioControlValidationView; type Error = StudioControlError; const NAME: &'static str = "studio.graphs.validate"; }

pub struct StudioControlWorkflowCreate;
impl Operation for StudioControlWorkflowCreate { type Request = StudioControlWorkflowCreateRequest; type Response = StudioControlWorkflowSaveResult; type Error = StudioControlError; const NAME: &'static str = "studio.workflows.create"; }

pub struct StudioControlWorkflowUpdate;
impl Operation for StudioControlWorkflowUpdate { type Request = StudioControlWorkflowUpdateRequest; type Response = StudioControlWorkflowSaveResult; type Error = StudioControlError; const NAME: &'static str = "studio.workflows.update"; }

pub struct StudioControlWorkflowGet;
impl Operation for StudioControlWorkflowGet { type Request = StudioControlWorkflowGetRequest; type Response = StudioControlWorkflowView; type Error = StudioControlError; const NAME: &'static str = "studio.workflows.get"; }

pub struct StudioControlWorkflowList;
impl Operation for StudioControlWorkflowList { type Request = StudioControlListRequest; type Response = StudioControlReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.workflows.list"; }

pub struct StudioControlSelectionCreate;
impl Operation for StudioControlSelectionCreate { type Request = StudioControlSelectionCreateRequest; type Response = StudioControlSelectionView; type Error = StudioControlError; const NAME: &'static str = "studio.selections.create"; }

pub struct StudioControlSelectionAppend;
impl Operation for StudioControlSelectionAppend { type Request = StudioControlSelectionAppendRequest; type Response = StudioControlSelectionView; type Error = StudioControlError; const NAME: &'static str = "studio.selections.append"; }

pub struct StudioControlSelectionSeal;
impl Operation for StudioControlSelectionSeal { type Request = StudioControlSelectionSealRequest; type Response = StudioControlSelectionView; type Error = StudioControlError; const NAME: &'static str = "studio.selections.seal"; }

pub struct StudioControlRunSubmit;
impl Operation for StudioControlRunSubmit { type Request = StudioControlRunSubmitRequest; type Response = StudioControlRunView; type Error = StudioControlError; const NAME: &'static str = "studio.runs.submit"; }

pub struct StudioControlRunGet;
impl Operation for StudioControlRunGet { type Request = StudioControlRunGetRequest; type Response = StudioControlRunView; type Error = StudioControlError; const NAME: &'static str = "studio.runs.get"; }

pub struct StudioControlRunList;
impl Operation for StudioControlRunList { type Request = StudioControlListRequest; type Response = StudioControlReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.runs.list"; }

pub struct StudioControlRunNodesList;
impl Operation for StudioControlRunNodesList { type Request = StudioControlRunNodeListRequest; type Response = StudioControlReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.runs.nodes.list"; }

pub struct StudioControlRunCancel;
impl Operation for StudioControlRunCancel { type Request = StudioControlRunCancelRequest; type Response = StudioControlRunMutationResult; type Error = StudioControlError; const NAME: &'static str = "studio.runs.cancel"; }

pub struct StudioControlRunResume;
impl Operation for StudioControlRunResume { type Request = StudioControlRunResumeRequest; type Response = StudioControlRunMutationResult; type Error = StudioControlError; const NAME: &'static str = "studio.runs.resume"; }

pub struct StudioControlArtifactList;
impl Operation for StudioControlArtifactList { type Request = StudioControlArtifactListRequest; type Response = StudioControlReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.runs.artifacts.list"; }

pub struct StudioControlArtifactInspect;
impl Operation for StudioControlArtifactInspect { type Request = StudioControlArtifactInspectRequest; type Response = StudioControlArtifactView; type Error = StudioControlError; const NAME: &'static str = "studio.artifacts.inspect"; }

pub struct StudioControlArtifactRead;
impl Operation for StudioControlArtifactRead { type Request = StudioControlArtifactReadRequest; type Response = StudioControlArtifactChunk; type Error = StudioControlError; const NAME: &'static str = "studio.artifacts.read"; }

pub struct StudioControlLogsRead;
impl Operation for StudioControlLogsRead { type Request = StudioControlRunNodeDataRequest; type Response = StudioControlRunNodeDataView; type Error = StudioControlError; const NAME: &'static str = "studio.runs.logs.read"; }

pub struct StudioControlMetricsRead;
impl Operation for StudioControlMetricsRead { type Request = StudioControlRunNodeDataRequest; type Response = StudioControlRunNodeDataView; type Error = StudioControlError; const NAME: &'static str = "studio.runs.metrics.read"; }

pub struct StudioGraphBegin;
impl Operation for StudioGraphBegin { type Request = StudioGraphBeginRequest; type Response = StudioGraphDraftView; type Error = StudioControlError; const NAME: &'static str = "studio.ir.begin"; }

pub struct StudioGraphAppend;
impl Operation for StudioGraphAppend { type Request = StudioGraphAppendRequest; type Response = StudioGraphDraftView; type Error = StudioControlError; const NAME: &'static str = "studio.ir.append"; }

pub struct StudioGraphSeal;
impl Operation for StudioGraphSeal { type Request = StudioGraphSealRequest; type Response = StudioGraphSealResult; type Error = StudioControlError; const NAME: &'static str = "studio.ir.seal"; }

pub struct StudioGraphRead;
impl Operation for StudioGraphRead { type Request = StudioGraphReadRequest; type Response = StudioGraphReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.ir.read"; }

pub struct StudioGraphScalarBegin;
impl Operation for StudioGraphScalarBegin { type Request = StudioGraphScalarBeginRequest; type Response = StudioGraphScalarUploadView; type Error = StudioControlError; const NAME: &'static str = "studio.scalars.begin"; }

pub struct StudioGraphScalarWrite;
impl Operation for StudioGraphScalarWrite { type Request = StudioGraphScalarWriteRequest; type Response = StudioGraphScalarUploadView; type Error = StudioControlError; const NAME: &'static str = "studio.scalars.write"; }

pub struct StudioGraphScalarSeal;
impl Operation for StudioGraphScalarSeal { type Request = StudioGraphScalarSealRequest; type Response = StudioGraphScalarRef; type Error = StudioControlError; const NAME: &'static str = "studio.scalars.seal"; }

pub struct StudioGraphScalarRead;
impl Operation for StudioGraphScalarRead { type Request = StudioGraphScalarReadRequest; type Response = StudioGraphScalarChunk; type Error = StudioControlError; const NAME: &'static str = "studio.scalars.read"; }

pub struct StudioGraphSelectionResolve;
impl Operation for StudioGraphSelectionResolve { type Request = StudioGraphSelectionResolveRequest; type Response = StudioGraphSelectionSnapshot; type Error = StudioControlError; const NAME: &'static str = "studio.ir.selection.resolve"; }

pub struct StudioGraphSelectionRead;
impl Operation for StudioGraphSelectionRead { type Request = StudioGraphSelectionReadRequest; type Response = StudioGraphSelectionEvent; type Error = StudioControlError; const NAME: &'static str = "studio.ir.selection.read"; }

pub struct StudioValueList;
impl Operation for StudioValueList { type Request = StudioValueListRequest; type Response = StudioValueListEvent; type Error = StudioControlError; const NAME: &'static str = "studio.values.list"; }

pub struct StudioValueRead;
impl Operation for StudioValueRead { type Request = StudioValueReadRequest; type Response = StudioValueOutputView; type Error = StudioControlError; const NAME: &'static str = "studio.values.read"; }

pub struct StudioValueTextRead;
impl Operation for StudioValueTextRead { type Request = StudioValueTextReadRequest; type Response = StudioValueTextChunk; type Error = StudioControlError; const NAME: &'static str = "studio.values.text.read"; }

pub struct StudioValueLiteralRead;
impl Operation for StudioValueLiteralRead { type Request = StudioValueLiteralReadRequest; type Response = StudioValueLiteralEvent; type Error = StudioControlError; const NAME: &'static str = "studio.values.literal.read"; }

pub struct StudioValueRowsRead;
impl Operation for StudioValueRowsRead { type Request = StudioValueRowsReadRequest; type Response = StudioValueRowsEvent; type Error = StudioControlError; const NAME: &'static str = "studio.values.rows.read"; }

pub struct StudioValueMetricsRead;
impl Operation for StudioValueMetricsRead { type Request = StudioValueMetricsReadRequest; type Response = StudioValueHistoryReadEvent; type Error = StudioControlError; const NAME: &'static str = "studio.metrics.read"; }

pub struct TaskExtensionScopeReplace;
impl Operation for TaskExtensionScopeReplace { type Request = TaskExtensionScopeReplaceRequest; type Response = TaskExtensionScopeView; type Error = TaskContractError; const NAME: &'static str = "tasks.scope.replace"; }

pub struct TaskExtensionScopeInspect;
impl Operation for TaskExtensionScopeInspect { type Request = TaskExtensionScopeInspectRequest; type Response = TaskExtensionOptionalScopeView; type Error = TaskContractError; const NAME: &'static str = "tasks.scope.inspect"; }

pub struct TaskExtensionRecordsMerge;
impl Operation for TaskExtensionRecordsMerge { type Request = TaskExtensionRecordMergeRequest; type Response = TaskExtensionRecordReceipt; type Error = TaskContractError; const NAME: &'static str = "tasks.records.merge"; }

pub struct TaskExtensionRecordsList;
impl Operation for TaskExtensionRecordsList { type Request = TaskExtensionRecordsListRequest; type Response = TaskExtensionRecordsEvent; type Error = TaskContractError; const NAME: &'static str = "tasks.records.list"; }

pub struct CoordinationReservationsBegin;
impl Operation for CoordinationReservationsBegin { type Request = CoordinationReservationBeginRequest; type Response = CoordinationReservationView; type Error = TaskContractError; const NAME: &'static str = "claims.reservations.begin"; }

pub struct CoordinationReservationsInputSet;
impl Operation for CoordinationReservationsInputSet { type Request = CoordinationReservationInputRequest; type Response = CoordinationReservationInputReceipt; type Error = TaskContractError; const NAME: &'static str = "claims.reservations.input.set"; }

pub struct CoordinationReservationsCommit;
impl Operation for CoordinationReservationsCommit { type Request = CoordinationReservationCommitRequest; type Response = CoordinationReservationView; type Error = TaskContractError; const NAME: &'static str = "claims.reservations.commit"; }

pub struct CoordinationReservationsCancel;
impl Operation for CoordinationReservationsCancel { type Request = CoordinationReservationCancelRequest; type Response = CoordinationReservationView; type Error = TaskContractError; const NAME: &'static str = "claims.reservations.cancel"; }

pub struct CoordinationReservationsInspect;
impl Operation for CoordinationReservationsInspect { type Request = CoordinationReservationInspectRequest; type Response = CoordinationReservationEvent; type Error = TaskContractError; const NAME: &'static str = "claims.reservations.inspect"; }

pub struct TasksCreate;
impl Operation for TasksCreate { type Request = TaskCreateRequest; type Response = TaskView; type Error = TaskContractError; const NAME: &'static str = "tasks.create"; }

pub struct TasksList;
impl Operation for TasksList { type Request = TaskListRequest; type Response = TaskReadEvent; type Error = TaskContractError; const NAME: &'static str = "tasks.list"; }

pub struct TasksGet;
impl Operation for TasksGet { type Request = TaskGetRequest; type Response = TaskReadEvent; type Error = TaskContractError; const NAME: &'static str = "tasks.get"; }

pub struct TasksWatchersList;
impl Operation for TasksWatchersList { type Request = TaskGetRequest; type Response = TaskReadEvent; type Error = TaskContractError; const NAME: &'static str = "tasks.watchers.list"; }

pub struct TasksWatchersSet;
impl Operation for TasksWatchersSet { type Request = TaskWatchSetRequest; type Response = TaskWatcherView; type Error = TaskContractError; const NAME: &'static str = "tasks.watchers.set"; }

pub struct TasksAssign;
impl Operation for TasksAssign { type Request = TaskAssignRequest; type Response = TaskAllocationView; type Error = TaskContractError; const NAME: &'static str = "tasks.assign"; }

pub struct TasksClaim;
impl Operation for TasksClaim { type Request = TaskClaimRequest; type Response = TaskLeaseView; type Error = TaskContractError; const NAME: &'static str = "tasks.claim"; }

pub struct TasksHeartbeat;
impl Operation for TasksHeartbeat { type Request = TaskHeartbeatRequest; type Response = TaskLeaseView; type Error = TaskContractError; const NAME: &'static str = "tasks.heartbeat"; }

pub struct TasksCheckpoint;
impl Operation for TasksCheckpoint { type Request = TaskCheckpointRequest; type Response = TaskCheckpointView; type Error = TaskContractError; const NAME: &'static str = "tasks.checkpoint"; }

pub struct TasksSubmit;
impl Operation for TasksSubmit { type Request = TaskSubmitRequest; type Response = TaskProposalView; type Error = TaskContractError; const NAME: &'static str = "tasks.submit"; }

pub struct TasksReview;
impl Operation for TasksReview { type Request = TaskReviewRequest; type Response = TaskReviewView; type Error = TaskContractError; const NAME: &'static str = "tasks.review"; }

pub struct TasksBlock;
impl Operation for TasksBlock { type Request = TaskBlockRequest; type Response = TaskView; type Error = TaskContractError; const NAME: &'static str = "tasks.block"; }

pub struct TasksReady;
impl Operation for TasksReady { type Request = TaskReadyRequest; type Response = TaskView; type Error = TaskContractError; const NAME: &'static str = "tasks.ready"; }

pub struct TasksModeInspect;
impl Operation for TasksModeInspect { type Request = TaskModeInspectRequest; type Response = TaskModeView; type Error = TaskContractError; const NAME: &'static str = "tasks.mode.inspect"; }

pub struct TasksModeSet;
impl Operation for TasksModeSet { type Request = TaskModeSetRequest; type Response = TaskModeView; type Error = TaskContractError; const NAME: &'static str = "tasks.mode.set"; }

pub struct TasksComplete;
impl Operation for TasksComplete { type Request = TaskCompleteRequest; type Response = TaskView; type Error = TaskContractError; const NAME: &'static str = "tasks.complete"; }

pub struct TasksAdmitInspect;
impl Operation for TasksAdmitInspect { type Request = TaskAdmitInspectRequest; type Response = TaskView; type Error = TaskContractError; const NAME: &'static str = "tasks.admit.inspect"; }

pub struct CoordinationClaimsTake;
impl Operation for CoordinationClaimsTake { type Request = CoordinationClaimTakeRequest; type Response = CoordinationClaimTakeResult; type Error = TaskContractError; const NAME: &'static str = "claims.take"; }

pub struct CoordinationClaimsRelease;
impl Operation for CoordinationClaimsRelease { type Request = CoordinationClaimReleaseRequest; type Response = CoordinationClaimReleaseResult; type Error = TaskContractError; const NAME: &'static str = "claims.release"; }

pub struct CoordinationClaimsList;
impl Operation for CoordinationClaimsList { type Request = CoordinationClaimsListRequest; type Response = CoordinationClaimsEvent; type Error = TaskContractError; const NAME: &'static str = "claims.list"; }
