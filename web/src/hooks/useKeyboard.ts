import { useEffect } from 'react'
import { useEditor } from '../state/store'

/** Coarse/fine nudge in document points. */
const NUDGE = 1
const NUDGE_LARGE = 10

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  return (
    el.tagName === 'INPUT' ||
    el.tagName === 'TEXTAREA' ||
    el.tagName === 'SELECT' ||
    el.isContentEditable
  )
}

/** Global editor shortcuts. Suppressed while a form control has focus. */
export function useKeyboard() {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return
      const s = useEditor.getState()
      const mod = e.metaKey || e.ctrlKey

      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) s.redo()
        else s.undo()
        return
      }
      if (mod && e.key.toLowerCase() === 'a') {
        e.preventDefault()
        s.selectAll()
        return
      }
      if (mod && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        s.duplicateSelected()
        return
      }
      if (mod && e.key === ']') {
        e.preventDefault()
        s.reorderSelected(e.shiftKey ? 'front' : 'forward')
        return
      }
      if (mod && e.key === '[') {
        e.preventDefault()
        s.reorderSelected(e.shiftKey ? 'back' : 'backward')
        return
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        s.deleteSelected()
        return
      }
      if (e.key === 'Escape') {
        s.clearSelection()
        return
      }

      const step = e.shiftKey ? NUDGE_LARGE : NUDGE
      const arrows: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
      }
      const delta = arrows[e.key]
      if (delta && s.selection.length > 0) {
        e.preventDefault()
        // Snapshot once per key-press burst rather than per repeat, so holding
        // an arrow key produces one undo entry, not fifty.
        if (!e.repeat) s.pushHistory()
        s.nudgeSelected(delta[0], delta[1])
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}
