export function isBackEdge(edges, edge) {
  if (edge.source === edge.target) return true
  const seen = new Set()
  const stack = [edge.target]
  while (stack.length) {
    const u = stack.pop()
    if (seen.has(u)) continue
    seen.add(u)
    if (u === edge.source) return true
    for (const e of edges) {
      if (e === edge || (e.id && e.id === edge.id)) continue
      if (e.source === u) stack.push(e.target)
    }
  }
  return false
}

export function typesOk(src, dst) {
  if (!src || !dst || src === 'any' || dst === 'any') return true
  return src === dst
}

export function portType(nodeTypes, node, handle, io) {
  const spec = nodeTypes.find(t => t.type === (node.data && node.data.nodeType))
  if (!spec) return 'any'
  const params = (node.data && node.data.params) || {}
  let ports = io === 'in' ? spec.inputs : spec.outputs
  if (io === 'in' && spec.dynamic_inputs === 'slots') {
    const slots = params.slots && params.slots.length ? params.slots : ['a']
    ports = slots.map(s => ({ name: s, json_type: 'any' }))
  }
  if (io === 'in' && spec.dynamic_inputs === 'ports') {
    const names = params.ports && params.ports.length ? params.ports : ['a', 'b']
    ports = names.map(s => ({ name: s, json_type: 'any' }))
  }
  const p = (ports || []).find(x => x.name === handle)
  return (p && p.json_type) || 'any'
}

export function inputPorts(spec, params) {
  if (!spec) return []
  params = params || {}
  if (spec.dynamic_inputs === 'slots') {
    const slots = (params.slots && params.slots.length) ? params.slots : ['a']
    return slots.map(s => ({ name: s, json_type: 'any', label: s }))
  }
  if (spec.dynamic_inputs === 'ports') {
    const names = (params.ports && params.ports.length) ? params.ports : ['a', 'b']
    return names.map(s => ({ name: s, json_type: 'any', label: s }))
  }
  return spec.inputs || []
}

export function toFlow(meta, nodes, edges, groups) {
  return {
    id: meta.id,
    name: meta.name,
    nodes: nodes.map(n => ({
      id: n.id,
      type: n.data.nodeType,
      x: n.position.x,
      y: n.position.y,
      params: n.data.params || {},
      breakpoints: n.data.breakpoints || {},
    })),
    edges: edges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      source_port: e.sourceHandle,
      target_port: e.targetHandle,
      kind: 'data',
    })),
    groups: groups || [],
    adapter_id: meta.adapter_id,
    action_list_version: meta.action_list_version,
  }
}

export function fromFlow(flow, nodeTypes) {
  const nodes = (flow.nodes || []).map(n => {
    const spec = (nodeTypes || []).find(t => t.type === n.type)
    return {
      id: n.id,
      type: 'agent',
      position: { x: n.x || 0, y: n.y || 0 },
      data: {
        nodeType: n.type,
        title: n.title || '',
        params: n.params || (spec && spec.default_params) || {},
        breakpoints: n.breakpoints || {},
      },
    }
  })
  const edges = (flow.edges || []).map(e => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.source_port,
    targetHandle: e.target_port,
    type: 'default',
    markerEnd: 'arrowclosed',
  }))
  for (const e of edges) if (isBackEdge(edges, e)) e.type = 'loop'
  return {
    nodes, edges, groups: flow.groups || [], name: flow.name, id: flow.id, version: flow.version,
    adapter_id: flow.adapter_id, action_list_version: flow.action_list_version,
  }
}
