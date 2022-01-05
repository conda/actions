export interface Item {
  id: number
  number: number
  title: string
}

export interface Owner {
  org?: string | null
  user?: string | null
  repo?: string | null
}
