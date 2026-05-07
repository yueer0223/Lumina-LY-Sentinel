import chromadb


class SentinelBrain:
    def __init__(self):
        self.client = chromadb.PersistentClient(path="./.sentinel_memory")
        self.collection = self.client.get_or_create_collection(name="code_style")

    def memorize(self, filename, content):
        lines = content.splitlines()
        chunk_size = 50
        chunks = []
        metadatas = []
        ids = []

        for i in range(0, len(lines), chunk_size):
            chunk_text = "\n".join(lines[i:i + chunk_size])
            chunk_id = f"{filename}_part_{i // chunk_size}"
            chunks.append(chunk_text)
            metadatas.append({"source": filename, "part": i // chunk_size})
            ids.append(chunk_id)

        try:
            existing_data = self.collection.get(where={"source": filename})
            if existing_data and existing_data['ids']:
                self.collection.delete(ids=existing_data['ids'])
        except Exception:
            pass

        if chunks:
            self.collection.add(documents=chunks, metadatas=metadatas, ids=ids)

    def count_memories(self):
        return self.collection.count()

    def recall(self, question, n_results=1):
        return self.collection.query(query_texts=[question], n_results=n_results)

    # 🚨 新增：审查之眼 (Auto-Audit)
    def audit(self, new_code):
        """对比新代码与历史代码库的向量距离，找出异体代码"""
        if self.count_memories() == 0:
            return True, 0.0  # 空大脑，默认安全

        # 用新代码撞击记忆库
        results = self.collection.query(
            query_texts=[new_code[:500]],  # 取前500字符即可鉴定风格
            n_results=1
        )
        if results['distances'] and results['distances'][0]:
            distance = results['distances'][0][0]
            # ChromaDB 的距离值越小越相似。大于 1.2 通常说明风格突变！
            is_safe = distance < 1.2
            return is_safe, distance
        return True, 0.0