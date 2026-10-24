// Generated proposal. No dispatch or authority is implemented. Schema b8a8753879f337a4673e8e1e844fd2768bda48e6e716607fa14da9368a039700
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
