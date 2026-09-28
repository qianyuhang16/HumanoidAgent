"""Replay the observed bowl -> cup -> plan bowl ordering without physical tools."""
import unittest
import test_agent

class MultipleTargetsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_agent.AgentToolsTests.asyncSetUp
    call=test_agent.AgentToolsTests.call

    async def test_two_objects_from_one_observation_remain_plannable(self):
        obs=(await self.provider.observe({},self.call())).data['observation_id']
        bowl=await self.provider.segment({'observation_id':obs,'query':'pink bowl'},self.call())
        self.inference.pixel=[325.,240.]
        cup=await self.provider.segment({'observation_id':obs,'query':'blue cup'},self.call())
        result=await self.provider.plan({'detection_id':bowl.data['detection_id'],'arm':'left'},self.call())
        self.assertTrue(result.data['solved'],result.data)
        self.assertIn(cup.data['detection_id'],self.provider.detections)

    async def test_failed_other_query_retains_bowl_but_refresh_invalidates_all(self):
        obs=(await self.provider.observe({},self.call())).data['observation_id']
        bowl=await self.provider.segment({'observation_id':obs,'query':'pink bowl'},self.call())
        self.inference.found=False
        await self.provider.segment({'observation_id':obs,'query':'blue cup'},self.call())
        self.assertIn(bowl.data['detection_id'],self.provider.detections)
        await self.provider.observe({},self.call())
        self.assertEqual(self.provider.detections,{})

    async def test_failed_same_query_replaces_only_its_token(self):
        obs=(await self.provider.observe({},self.call())).data['observation_id']
        bowl=await self.provider.segment({'observation_id':obs,'query':'pink bowl'},self.call())
        cup=await self.provider.segment({'observation_id':obs,'query':'blue cup'},self.call())
        self.inference.found=False
        await self.provider.segment({'observation_id':obs,'query':' Pink  Bowl '},self.call())
        self.assertNotIn(bowl.data['detection_id'],self.provider.detections)
        self.assertIn(cup.data['detection_id'],self.provider.detections)
