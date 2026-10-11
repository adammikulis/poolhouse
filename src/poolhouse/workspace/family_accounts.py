"""Model-family accounts linked to actual task model resources."""

from poolhouse.client.families import GENERIC, for_model_id


def binding(workspace, resource):
    """The model-family account that ``resource``'s model earns credit to on board ``workspace``, or None for a generic model."""
    family = for_model_id(resource.get('model'))
    if family is GENERIC:
        return None
    return {'id': f'family:{workspace}:{family.name}', 'account': f'family-{family.name}',
            'family_id': family.name, 'label': {'qwen':'Qwen','gemma':'Gemma','gpt-oss':'GPT OSS'}[family.name],
            'workspace': workspace, 'source': 'verified-serving-resource'}
